"""
Expected Settlement Calculator and Timestamp Normalization Engine.

Implements calendar normalization, cutoff evaluation, and health classification:
- Event Time (T_event): Authorization timestamp
- Booking Time (T_booking): Core ledger posting timestamp
- Value Time (T_value): Legally cleared and available funds timestamp

Maps UTC events to local banking schedules and determines:
- ON_SCHEDULE vs. LEGITIMATELY_DELAYED vs. SLA_BREACHED vs. FAILED.
"""

from datetime import datetime, date, time, timedelta
from typing import Dict, Optional, Tuple
import zoneinfo

from .models import (
    RailType,
    RailScheduleConfig,
    TimestampLifecycle,
    PaymentTransaction,
    EvaluationResult,
    SettlementHealth,
    DelayReason,
)
from .calendar import BankingCalendar


def get_default_rail_configs() -> Dict[RailType, RailScheduleConfig]:
    """Provides production-standard default schedules for Indian and interbank rails."""
    return {
        RailType.NEFT: RailScheduleConfig(
            rail=RailType.NEFT,
            timezone_name="Asia/Kolkata",
            start_time=time(0, 0),
            cutoff_time=time(23, 0),      # Daily EOD clearing cutoff for same-day value
            is_24x7=True,
            batch_interval_minutes=30,    # 48 half-hourly batches
            instant_settlement=False,
            min_amount=1.0,
            max_amount=None,
            expected_booking_lag_seconds=2,
            expected_settlement_lag_minutes=25, # Clears at batch end + credit notification
            grace_period_minutes=45,
            max_timeout_hours=24,
        ),
        RailType.RTGS: RailScheduleConfig(
            rail=RailType.RTGS,
            timezone_name="Asia/Kolkata",
            start_time=time(0, 0),
            cutoff_time=time(23, 30),     # Continuous gross settlement with EOD reconciliation
            is_24x7=True,
            batch_interval_minutes=None,  # Continuous
            instant_settlement=False,
            min_amount=200000.0,          # Minimum INR 2,00,000
            max_amount=None,
            expected_booking_lag_seconds=1,
            expected_settlement_lag_minutes=10, # Real-time typically settles within 5-15 mins
            grace_period_minutes=30,
            max_timeout_hours=12,
        ),
        RailType.IMPS: RailScheduleConfig(
            rail=RailType.IMPS,
            timezone_name="Asia/Kolkata",
            start_time=time(0, 0),
            cutoff_time=None,             # Pure 24x7x365
            is_24x7=True,
            batch_interval_minutes=None,
            instant_settlement=True,
            min_amount=1.0,
            max_amount=500000.0,
            expected_booking_lag_seconds=1,
            expected_settlement_lag_minutes=1,
            grace_period_minutes=5,
            max_timeout_hours=4,
        ),
        RailType.UPI: RailScheduleConfig(
            rail=RailType.UPI,
            timezone_name="Asia/Kolkata",
            start_time=time(0, 0),
            cutoff_time=None,             # Pure 24x7x365
            is_24x7=True,
            batch_interval_minutes=None,
            instant_settlement=True,
            min_amount=1.0,
            max_amount=100000.0,
            expected_booking_lag_seconds=1,
            expected_settlement_lag_minutes=1,
            grace_period_minutes=3,
            max_timeout_hours=2,
        ),
        RailType.ACH: RailScheduleConfig(
            rail=RailType.ACH,
            timezone_name="Asia/Kolkata",
            start_time=time(8, 0),
            cutoff_time=time(16, 0),      # Strict 16:00 business-day cutoff
            is_24x7=False,                # Observes banking holidays & weekends
            batch_interval_minutes=None,  # Daily batch / Next business day T+1
            instant_settlement=False,
            min_amount=1.0,
            max_amount=None,
            expected_booking_lag_seconds=5,
            expected_settlement_lag_minutes=60,
            grace_period_minutes=180,
            max_timeout_hours=72,
        ),
    }


class ExpectedSettlementCalculator:
    """
    Core calculator for Expected Settlement Timestamps and Health Verification.
    """

    def __init__(
        self,
        calendar: Optional[BankingCalendar] = None,
        rail_configs: Optional[Dict[RailType, RailScheduleConfig]] = None
    ):
        self.calendar = calendar or BankingCalendar()
        self.rail_configs = rail_configs or get_default_rail_configs()

    def normalize_utc(self, dt: datetime) -> datetime:
        """Ensure datetime has explicit UTC timezone."""
        if dt.tzinfo is None:
            return dt.replace(tzinfo=zoneinfo.ZoneInfo("UTC"))
        return dt.astimezone(zoneinfo.ZoneInfo("UTC"))

    def compute_booking_time(
        self,
        event_time_utc: datetime,
        config: RailScheduleConfig
    ) -> datetime:
        """
        Calculate Booking Time (ledger debit/credit entry).
        In automated systems, booking happens near-instantaneously after authorization.
        """
        return event_time_utc + timedelta(seconds=config.expected_booking_lag_seconds)

    def _get_next_neft_batch(self, local_dt: datetime) -> datetime:
        """
        Calculate the next half-hourly NEFT batch cutoff time.
        Batches occur at :00 and :30 of every hour (00:30, 01:00, ..., 23:30, 00:00).
        """
        minute = local_dt.minute
        second = local_dt.second
        microsecond = local_dt.microsecond

        # If exactly on the boundary with 0 seconds/microsecond, it's the current batch cutoff
        if minute in (0, 30) and second == 0 and microsecond == 0:
            return local_dt

        if minute < 30:
            # Next batch is at :30 of current hour
            return local_dt.replace(minute=30, second=0, microsecond=0)
        else:
            # Next batch is at :00 of next hour
            base = local_dt.replace(minute=0, second=0, microsecond=0)
            return base + timedelta(hours=1)

    def calculate_timestamps(
        self,
        event_time_utc: datetime,
        rail: RailType,
        amount: float
    ) -> Tuple[TimestampLifecycle, DelayReason, str, Optional[datetime]]:
        """
        Computes normalized Event, Booking, and Value Timestamps based on rail schedules,
        banking cutoff times, and holiday calendars.

        Returns:
            (lifecycle, delay_reason, delay_description, next_clearing_window_local)
        """
        config = self.rail_configs.get(rail)
        if not config:
            raise ValueError(f"Unsupported rail type: {rail}")

        event_utc = self.normalize_utc(event_time_utc)
        booking_utc = self.compute_booking_time(event_utc, config)

        # Convert to local banking timezone for calendar/cutoff comparison
        event_local = self.calendar.to_local(event_utc)
        booking_local = self.calendar.to_local(booking_utc)

        delay_reason = DelayReason.NONE
        delay_description = "Transaction processed within normal operating cycle"
        next_clearing_window_local: Optional[datetime] = None
        cutoff_local: Optional[datetime] = None

        # 1. Check Amount Thresholds (e.g., RTGS min INR 2,00,000)
        if config.min_amount is not None and amount < config.min_amount:
            delay_reason = DelayReason.AMOUNT_THRESHOLD_VIOLATION
            delay_description = (
                f"Amount {amount:,.2f} is below minimum {config.min_amount:,.2f} required for {rail.value}. "
                f"Requires manual STP routing or fallback rail."
            )
            # Default fallback calculation
            expected_val_local = event_local + timedelta(hours=4)
            expected_val_utc = self.calendar.to_utc(expected_val_local)
            sla_utc = expected_val_utc + timedelta(minutes=config.grace_period_minutes)
            
            lifecycle = TimestampLifecycle(
                event_time_utc=event_utc,
                booking_time_utc=booking_utc,
                expected_value_time_utc=expected_val_utc,
                sla_deadline_utc=sla_utc,
                cutoff_time_utc=None,
                actual_value_time_utc=None,
                timezone_name=self.calendar.timezone_name,
                event_time_local=event_local,
                booking_time_local=booking_local,
                expected_value_time_local=expected_val_local,
                sla_deadline_local=self.calendar.to_local(sla_utc),
                cutoff_time_local=None,
            )
            return lifecycle, delay_reason, delay_description, None

        # 2. Instant Rails (UPI / IMPS) - 24x7 Instant Settlement
        if config.instant_settlement:
            expected_val_utc = event_utc + timedelta(minutes=config.expected_settlement_lag_minutes)
            sla_utc = expected_val_utc + timedelta(minutes=config.grace_period_minutes)
            
            lifecycle = TimestampLifecycle(
                event_time_utc=event_utc,
                booking_time_utc=booking_utc,
                expected_value_time_utc=expected_val_utc,
                sla_deadline_utc=sla_utc,
                cutoff_time_utc=None,
                timezone_name=self.calendar.timezone_name,
                event_time_local=event_local,
                booking_time_local=booking_local,
                expected_value_time_local=self.calendar.to_local(expected_val_utc),
                sla_deadline_local=self.calendar.to_local(sla_utc),
            )
            return lifecycle, DelayReason.NONE, "Instant 24x7 rail settlement", None

        # 3. Rails that observe Banking Days (e.g., ACH / Batch Rails)
        if not config.is_24x7:
            current_date = event_local.date()
            is_working, holiday_reason = self.calendar.is_banking_day(current_date)
            
            target_date = current_date
            rolled_over = False

            if not is_working:
                # Event occurred on a bank holiday or weekend (Sunday / 2nd or 4th Saturday)
                rolled_over = True
                target_date, skip_desc = self.calendar.get_next_banking_day(current_date)
                
                if "Saturday" in holiday_reason:
                    delay_reason = DelayReason.WEEKEND_NON_WORKING
                elif "Sunday" in holiday_reason:
                    delay_reason = DelayReason.WEEKEND_NON_WORKING
                else:
                    delay_reason = DelayReason.PUBLIC_BANK_HOLIDAY
                
                delay_description = f"Initiated on non-banking day ({holiday_reason}). Rolled over to {target_date.isoformat()}."
            else:
                # On a working day, check if past cutoff time
                if config.cutoff_time:
                    cutoff_local = datetime.combine(current_date, config.cutoff_time, tzinfo=self.calendar.tz)
                    if event_local.time() > config.cutoff_time:
                        rolled_over = True
                        target_date, skip_desc = self.calendar.get_next_banking_day(current_date)
                        delay_reason = DelayReason.PAST_DAILY_CUTOFF
                        delay_description = (
                            f"Initiated at {event_local.strftime('%H:%M:%S')} past daily cutoff {config.cutoff_time.strftime('%H:%M:%S')}. "
                            f"Rolled over to next business day ({target_date.isoformat()})."
                        )

            # Expected Value time is target_date opening window + settlement lag
            opening_dt = datetime.combine(target_date, config.start_time, tzinfo=self.calendar.tz)
            expected_val_local = opening_dt + timedelta(minutes=config.expected_settlement_lag_minutes)
            expected_val_utc = self.calendar.to_utc(expected_val_local)
            sla_utc = expected_val_utc + timedelta(minutes=config.grace_period_minutes)
            next_clearing_window_local = opening_dt

            cutoff_utc = self.calendar.to_utc(cutoff_local) if cutoff_local else None

            lifecycle = TimestampLifecycle(
                event_time_utc=event_utc,
                booking_time_utc=booking_utc,
                expected_value_time_utc=expected_val_utc,
                sla_deadline_utc=sla_utc,
                cutoff_time_utc=cutoff_utc,
                timezone_name=self.calendar.timezone_name,
                event_time_local=event_local,
                booking_time_local=booking_local,
                expected_value_time_local=expected_val_local,
                sla_deadline_local=self.calendar.to_local(sla_utc),
                cutoff_time_local=cutoff_local,
            )
            return lifecycle, delay_reason, delay_description, next_clearing_window_local

        # 4. NEFT (24x7 Half-Hourly Batches)
        if rail == RailType.NEFT:
            # Check if event is past EOD cutoff (e.g. 23:00) for same-day ledger value
            current_date = event_local.date()
            if config.cutoff_time:
                cutoff_local = datetime.combine(current_date, config.cutoff_time, tzinfo=self.calendar.tz)
                if event_local.time() > config.cutoff_time:
                    # Still processes in subsequent night batches, but flagged as post-EOD cutoff
                    delay_reason = DelayReason.PAST_DAILY_CUTOFF
                    delay_description = (
                        f"Initiated at {event_local.strftime('%H:%M:%S')} after EOD cutoff "
                        f"{config.cutoff_time.strftime('%H:%M:%S')}. Next batch processed in overnight cycle."
                    )

            batch_cutoff_local = self._get_next_neft_batch(event_local)
            next_clearing_window_local = batch_cutoff_local
            
            # Settlement / Value Time is batch cutoff + clearance/credit lag
            expected_val_local = batch_cutoff_local + timedelta(minutes=config.expected_settlement_lag_minutes)
            expected_val_utc = self.calendar.to_utc(expected_val_local)
            sla_utc = expected_val_utc + timedelta(minutes=config.grace_period_minutes)
            cutoff_utc = self.calendar.to_utc(cutoff_local) if cutoff_local else None

            if delay_reason == DelayReason.NONE:
                delay_reason = DelayReason.BATCH_CYCLE_WAIT
                delay_description = f"Scheduled for NEFT batch window at {batch_cutoff_local.strftime('%H:%M:%S IST')}."

            lifecycle = TimestampLifecycle(
                event_time_utc=event_utc,
                booking_time_utc=booking_utc,
                expected_value_time_utc=expected_val_utc,
                sla_deadline_utc=sla_utc,
                cutoff_time_utc=cutoff_utc,
                timezone_name=self.calendar.timezone_name,
                event_time_local=event_local,
                booking_time_local=booking_local,
                expected_value_time_local=expected_val_local,
                sla_deadline_local=self.calendar.to_local(sla_utc),
                cutoff_time_local=cutoff_local,
            )
            return lifecycle, delay_reason, delay_description, next_clearing_window_local

        # 5. RTGS (Continuous Gross Settlement, 24x7 with EOD Cutoff)
        if rail == RailType.RTGS:
            current_date = event_local.date()
            if config.cutoff_time:
                cutoff_local = datetime.combine(current_date, config.cutoff_time, tzinfo=self.calendar.tz)
                if event_local.time() > config.cutoff_time:
                    delay_reason = DelayReason.PAST_DAILY_CUTOFF
                    delay_description = (
                        f"Initiated at {event_local.strftime('%H:%M:%S')} after RTGS daily cutoff "
                        f"{config.cutoff_time.strftime('%H:%M:%S')}."
                    )

            expected_val_local = event_local + timedelta(minutes=config.expected_settlement_lag_minutes)
            expected_val_utc = self.calendar.to_utc(expected_val_local)
            sla_utc = expected_val_utc + timedelta(minutes=config.grace_period_minutes)
            cutoff_utc = self.calendar.to_utc(cutoff_local) if cutoff_local else None

            lifecycle = TimestampLifecycle(
                event_time_utc=event_utc,
                booking_time_utc=booking_utc,
                expected_value_time_utc=expected_val_utc,
                sla_deadline_utc=sla_utc,
                cutoff_time_utc=cutoff_utc,
                timezone_name=self.calendar.timezone_name,
                event_time_local=event_local,
                booking_time_local=booking_local,
                expected_value_time_local=expected_val_local,
                sla_deadline_local=self.calendar.to_local(sla_utc),
                cutoff_time_local=cutoff_local,
            )
            return lifecycle, delay_reason, delay_description, None

        # Fallback for any other custom rail
        expected_val_utc = event_utc + timedelta(minutes=config.expected_settlement_lag_minutes)
        sla_utc = expected_val_utc + timedelta(minutes=config.grace_period_minutes)
        lifecycle = TimestampLifecycle(
            event_time_utc=event_utc,
            booking_time_utc=booking_utc,
            expected_value_time_utc=expected_val_utc,
            sla_deadline_utc=sla_utc,
            timezone_name=self.calendar.timezone_name,
            event_time_local=event_local,
            booking_time_local=booking_local,
            expected_value_time_local=self.calendar.to_local(expected_val_utc),
            sla_deadline_local=self.calendar.to_local(sla_utc),
        )
        return lifecycle, DelayReason.NONE, "Standard processing", None

    def evaluate_transaction(
        self,
        transaction: PaymentTransaction,
        as_of_time_utc: Optional[datetime] = None
    ) -> EvaluationResult:
        """
        Evaluates a transaction's current health status against expected settlement milestones:
        - ON_SCHEDULE
        - SETTLED
        - LEGITIMATELY_DELAYED
        - SLA_BREACHED
        - FAILED
        """
        as_of_utc = self.normalize_utc(as_of_time_utc or datetime.now(zoneinfo.ZoneInfo("UTC")))
        config = self.rail_configs[transaction.rail]

        # 1. Compute expected lifecycle timestamps
        lifecycle, initial_delay_reason, delay_desc, next_clearing = self.calculate_timestamps(
            event_time_utc=transaction.event_time_utc,
            rail=transaction.rail,
            amount=transaction.amount,
        )

        # Reconcile with actual booking / value time if provided
        if transaction.booking_time_utc:
            b_utc = self.normalize_utc(transaction.booking_time_utc)
            lifecycle.booking_time_utc = b_utc
            lifecycle.booking_time_local = self.calendar.to_local(b_utc)

        if transaction.actual_value_time_utc:
            v_utc = self.normalize_utc(transaction.actual_value_time_utc)
            lifecycle.actual_value_time_utc = v_utc
            lifecycle.actual_value_time_local = self.calendar.to_local(v_utc)

        # 2. Check for explicit rejection / technical NACK
        if transaction.is_rejected:
            return EvaluationResult(
                txn_id=transaction.txn_id,
                rail=transaction.rail,
                health=SettlementHealth.FAILED,
                is_legitimately_delayed=False,
                is_sla_breached=False,
                is_failed=True,
                primary_reason=DelayReason.NONE,
                reason_description=f"Transaction failed with rail rejection: {transaction.rejection_code or 'REJECTED'}",
                lifecycle=lifecycle,
                next_clearing_window_local=next_clearing,
                metadata={"as_of_time_utc": as_of_utc.isoformat()},
            )

        # 3. Check if already settled
        if transaction.actual_value_time_utc is not None:
            return EvaluationResult(
                txn_id=transaction.txn_id,
                rail=transaction.rail,
                health=SettlementHealth.SETTLED,
                is_legitimately_delayed=False,
                is_sla_breached=False,
                is_failed=False,
                primary_reason=DelayReason.NONE,
                reason_description=f"Transaction successfully settled at {lifecycle.actual_value_time_local.isoformat()}",
                lifecycle=lifecycle,
                next_clearing_window_local=next_clearing,
                metadata={"as_of_time_utc": as_of_utc.isoformat()},
            )

        # 4. Evaluate in-flight status relative to expected value time and SLA deadline
        time_until_expected_val = (lifecycle.expected_value_time_utc - as_of_utc).total_seconds()
        time_since_expected_val = (as_of_utc - lifecycle.expected_value_time_utc).total_seconds()
        time_since_sla_deadline = (as_of_utc - lifecycle.sla_deadline_utc).total_seconds()
        max_timeout_seconds = config.max_timeout_hours * 3600

        # Scenario A: Irrevocable Timeout (Exceeded maximum rail timeout)
        if time_since_expected_val > max_timeout_seconds:
            return EvaluationResult(
                txn_id=transaction.txn_id,
                rail=transaction.rail,
                health=SettlementHealth.FAILED,
                is_legitimately_delayed=False,
                is_sla_breached=True,
                is_failed=True,
                primary_reason=DelayReason.NONE,
                reason_description=(
                    f"Transaction exceeded maximum acceptable rail timeout of {config.max_timeout_hours} hours. "
                    f"Presumed lost or failed without beneficiary bank confirmation."
                ),
                lifecycle=lifecycle,
                next_clearing_window_local=next_clearing,
                metadata={
                    "as_of_time_utc": as_of_utc.isoformat(),
                    "hours_overdue": time_since_expected_val / 3600.0,
                },
            )

        # Scenario B: SLA Breached (Unjustified delay past SLA deadline)
        if time_since_sla_deadline > 0:
            return EvaluationResult(
                txn_id=transaction.txn_id,
                rail=transaction.rail,
                health=SettlementHealth.SLA_BREACHED,
                is_legitimately_delayed=False,
                is_sla_breached=True,
                is_failed=False,
                primary_reason=DelayReason.NONE,
                reason_description=(
                    f"Expected settlement deadline of {lifecycle.sla_deadline_local.strftime('%Y-%m-%d %H:%M:%S IST')} "
                    f"has passed by {int(time_since_sla_deadline // 60)} minutes without confirmation. Rail delay investigation required."
                ),
                lifecycle=lifecycle,
                next_clearing_window_local=next_clearing,
                metadata={
                    "as_of_time_utc": as_of_utc.isoformat(),
                    "minutes_past_sla": time_since_sla_deadline / 60.0,
                },
            )

        # Scenario C: Within grace period (past expected value time, but within grace buffer)
        if time_since_expected_val > 0 and time_since_sla_deadline <= 0:
            return EvaluationResult(
                txn_id=transaction.txn_id,
                rail=transaction.rail,
                health=SettlementHealth.LEGITIMATELY_DELAYED,
                is_legitimately_delayed=True,
                is_sla_breached=False,
                is_failed=False,
                primary_reason=DelayReason.BATCH_CYCLE_WAIT,
                reason_description=(
                    f"Processing within {config.grace_period_minutes}-minute network buffer. "
                    f"SLA deadline is {lifecycle.sla_deadline_local.strftime('%H:%M:%S IST')}."
                ),
                lifecycle=lifecycle,
                next_clearing_window_local=next_clearing,
                metadata={
                    "as_of_time_utc": as_of_utc.isoformat(),
                    "minutes_into_grace_period": time_since_expected_val / 60.0,
                },
            )

        # Scenario D: Legitimate delay due to calendar / cutoff rollover before expected value time
        if initial_delay_reason in (
            DelayReason.WEEKEND_NON_WORKING,
            DelayReason.PUBLIC_BANK_HOLIDAY,
            DelayReason.PAST_DAILY_CUTOFF,
            DelayReason.AMOUNT_THRESHOLD_VIOLATION,
        ):
            return EvaluationResult(
                txn_id=transaction.txn_id,
                rail=transaction.rail,
                health=SettlementHealth.LEGITIMATELY_DELAYED,
                is_legitimately_delayed=True,
                is_sla_breached=False,
                is_failed=False,
                primary_reason=initial_delay_reason,
                reason_description=delay_desc,
                lifecycle=lifecycle,
                next_clearing_window_local=next_clearing,
                metadata={
                    "as_of_time_utc": as_of_utc.isoformat(),
                    "minutes_until_expected": time_until_expected_val / 60.0,
                },
            )

        # Scenario E: In-flight and on-schedule
        return EvaluationResult(
            txn_id=transaction.txn_id,
            rail=transaction.rail,
            health=SettlementHealth.ON_SCHEDULE,
            is_legitimately_delayed=False,
            is_sla_breached=False,
            is_failed=False,
            primary_reason=DelayReason.NONE,
            reason_description=(
                f"In-flight on schedule. Expected value time at "
                f"{lifecycle.expected_value_time_local.strftime('%Y-%m-%d %H:%M:%S IST')}."
            ),
            lifecycle=lifecycle,
            next_clearing_window_local=next_clearing,
            metadata={
                "as_of_time_utc": as_of_utc.isoformat(),
                "minutes_until_expected": time_until_expected_val / 60.0,
            },
        )
