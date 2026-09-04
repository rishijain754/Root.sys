"""
Comprehensive unit and integration test suite for Expected Settlement Calculator.
Validates:
- Event Time, Booking Time, and Value Time lifecycle integrity
- 2nd and 4th Saturday Indian banking holiday rules
- NEFT batch calculation & cutoffs
- RTGS threshold & continuous settlement
- Legitimately delayed vs SLA breached vs Failed classification
"""

import unittest
from datetime import datetime, date, time, timedelta
import zoneinfo
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from settlement_calculator.models import (
    RailType,
    SettlementHealth,
    DelayReason,
    PaymentTransaction,
    TimestampLifecycle,
)
from settlement_calculator.calendar import BankingCalendar
from settlement_calculator.calculator import (
    ExpectedSettlementCalculator,
    get_default_rail_configs,
)


class TestBankingCalendar(unittest.TestCase):
    """Test banking calendar, weekend rules, and holiday rollovers."""

    def setUp(self):
        self.calendar = BankingCalendar(timezone_name="Asia/Kolkata")
        self.utc = zoneinfo.ZoneInfo("UTC")
        self.ist = zoneinfo.ZoneInfo("Asia/Kolkata")

    def test_saturday_occurrence_detection(self):
        """
        Verify 2026 September Saturdays:
        - Sept 5: 1st Saturday (Working)
        - Sept 12: 2nd Saturday (Holiday)
        - Sept 19: 3rd Saturday (Working)
        - Sept 26: 4th Saturday (Holiday)
        """
        # Sept 5, 2026 is 1st Saturday
        d_1st = date(2026, 9, 5)
        self.assertEqual(self.calendar.get_saturday_occurrence(d_1st), 1)
        self.assertFalse(self.calendar.is_second_or_fourth_saturday(d_1st))
        is_working, _ = self.calendar.is_banking_day(d_1st)
        self.assertTrue(is_working)

        # Sept 12, 2026 is 2nd Saturday
        d_2nd = date(2026, 9, 12)
        self.assertEqual(self.calendar.get_saturday_occurrence(d_2nd), 2)
        self.assertTrue(self.calendar.is_second_or_fourth_saturday(d_2nd))
        is_working, reason = self.calendar.is_banking_day(d_2nd)
        self.assertFalse(is_working)
        self.assertIn("2nd Saturday Banking Holiday", reason)

        # Sept 19, 2026 is 3rd Saturday
        d_3rd = date(2026, 9, 19)
        self.assertEqual(self.calendar.get_saturday_occurrence(d_3rd), 3)
        self.assertFalse(self.calendar.is_second_or_fourth_saturday(d_3rd))
        is_working, _ = self.calendar.is_banking_day(d_3rd)
        self.assertTrue(is_working)

        # Sept 26, 2026 is 4th Saturday
        d_4th = date(2026, 9, 26)
        self.assertEqual(self.calendar.get_saturday_occurrence(d_4th), 4)
        self.assertTrue(self.calendar.is_second_or_fourth_saturday(d_4th))
        is_working, reason = self.calendar.is_banking_day(d_4th)
        self.assertFalse(is_working)
        self.assertIn("4th Saturday Banking Holiday", reason)

    def test_5th_saturday_is_working(self):
        """May 2026 has 5 Saturdays: May 30 is 5th Saturday (Working day)."""
        d_5th = date(2026, 5, 30)
        self.assertEqual(self.calendar.get_saturday_occurrence(d_5th), 5)
        self.assertFalse(self.calendar.is_second_or_fourth_saturday(d_5th))
        is_working, _ = self.calendar.is_banking_day(d_5th)
        self.assertTrue(is_working)

    def test_sundays_are_always_holidays(self):
        d_sun = date(2026, 9, 6)
        is_working, reason = self.calendar.is_banking_day(d_sun)
        self.assertFalse(is_working)
        self.assertIn("Sunday", reason)

    def test_public_holidays(self):
        d_rep = date(2026, 1, 26)
        is_working, reason = self.calendar.is_banking_day(d_rep)
        self.assertFalse(is_working)
        self.assertIn("Republic Day", reason)

        d_apr1 = date(2026, 4, 1)
        is_working, reason = self.calendar.is_banking_day(d_apr1)
        self.assertFalse(is_working)
        self.assertIn("RBI Annual Bank Accounts Closing", reason)

    def test_get_next_banking_day_weekend_rollover(self):
        """From Friday Sept 11 (day before 2nd Saturday), next day should skip Sat(12) and Sun(13) to Mon Sept 14."""
        next_day, reason = self.calendar.get_next_banking_day(date(2026, 9, 11))
        self.assertEqual(next_day, date(2026, 9, 14))
        self.assertIn("2026-09-12", reason)
        self.assertIn("2026-09-13", reason)


class TestExpectedSettlementCalculator(unittest.TestCase):
    """Test timestamp lifecycle, rails, cutoffs, and delay vs. failure classification."""

    def setUp(self):
        self.calendar = BankingCalendar(timezone_name="Asia/Kolkata")
        self.calculator = ExpectedSettlementCalculator(calendar=self.calendar)
        self.utc = zoneinfo.ZoneInfo("UTC")
        self.ist = zoneinfo.ZoneInfo("Asia/Kolkata")

    def test_timestamp_lifecycle_distinction(self):
        """
        Verify distinct Event Time, Booking Time, and Value Time:
        Event Time: When authorized
        Booking Time: When ledger debits (e.g. +2s)
        Value Time: When bank funds clear
        """
        event_utc = datetime(2026, 9, 1, 10, 0, 0, tzinfo=self.utc)  # Tuesday 15:30 IST
        lifecycle, _, _, _ = self.calculator.calculate_timestamps(
            event_time_utc=event_utc,
            rail=RailType.NEFT,
            amount=50000.0,
        )

        # 1. Event Time
        self.assertEqual(lifecycle.event_time_utc, event_utc)
        self.assertEqual(lifecycle.event_time_local.hour, 15)
        self.assertEqual(lifecycle.event_time_local.minute, 30)

        # 2. Booking Time (Event + 2s)
        self.assertEqual(lifecycle.booking_time_utc, event_utc + timedelta(seconds=2))
        self.assertTrue(lifecycle.booking_time_utc > lifecycle.event_time_utc)

        # 3. Value Time (Batch end + settlement lag)
        self.assertTrue(lifecycle.expected_value_time_utc > lifecycle.booking_time_utc)
        self.assertEqual(lifecycle.expected_value_time_local.tzinfo.key, "Asia/Kolkata")

    def test_neft_batch_calculation(self):
        """
        NEFT half-hourly batches:
        Event at 14:12 IST -> next batch cutoff is 14:30 IST.
        Expected clearance lag = 25 minutes -> Expected Value Time = 14:55 IST.
        """
        event_local = datetime(2026, 9, 1, 14, 12, 0, tzinfo=self.ist)
        event_utc = event_local.astimezone(self.utc)

        lifecycle, delay_reason, desc, next_clearing = self.calculator.calculate_timestamps(
            event_time_utc=event_utc,
            rail=RailType.NEFT,
            amount=10000.0,
        )

        self.assertEqual(next_clearing, datetime(2026, 9, 1, 14, 30, 0, tzinfo=self.ist))
        self.assertEqual(lifecycle.expected_value_time_local, datetime(2026, 9, 1, 14, 55, 0, tzinfo=self.ist))
        self.assertEqual(delay_reason, DelayReason.BATCH_CYCLE_WAIT)

    def test_rtgs_minimum_amount_threshold(self):
        """RTGS requires minimum INR 200,000."""
        event_utc = datetime(2026, 9, 1, 6, 0, 0, tzinfo=self.utc)  # 11:30 IST
        
        # Below threshold: 50,000
        lifecycle, delay_reason, desc, _ = self.calculator.calculate_timestamps(
            event_time_utc=event_utc,
            rail=RailType.RTGS,
            amount=50000.0,
        )
        self.assertEqual(delay_reason, DelayReason.AMOUNT_THRESHOLD_VIOLATION)
        self.assertIn("below minimum", desc)

        # Valid amount: 500,000
        lifecycle_valid, delay_reason_valid, _, _ = self.calculator.calculate_timestamps(
            event_time_utc=event_utc,
            rail=RailType.RTGS,
            amount=500000.0,
        )
        self.assertEqual(delay_reason_valid, DelayReason.NONE)

    def test_ach_post_cutoff_rollover(self):
        """
        ACH cutoff is 16:00 IST.
        Event at 16:15 IST on Tuesday Sept 1 should roll over to Wednesday Sept 2 opening (08:00 IST).
        """
        event_local = datetime(2026, 9, 1, 16, 15, 0, tzinfo=self.ist)
        event_utc = event_local.astimezone(self.utc)

        lifecycle, delay_reason, desc, next_window = self.calculator.calculate_timestamps(
            event_time_utc=event_utc,
            rail=RailType.ACH,
            amount=25000.0,
        )

        self.assertEqual(delay_reason, DelayReason.PAST_DAILY_CUTOFF)
        self.assertIn("past daily cutoff", desc)
        self.assertEqual(next_window, datetime(2026, 9, 2, 8, 0, 0, tzinfo=self.ist))

    def test_ach_weekend_rollover_on_second_saturday(self):
        """
        ACH event on 2nd Saturday (Sept 12, 2026) at 10:00 IST.
        Should roll over Saturday and Sunday to Monday Sept 14, 08:00 IST.
        """
        event_local = datetime(2026, 9, 12, 10, 0, 0, tzinfo=self.ist)
        event_utc = event_local.astimezone(self.utc)

        lifecycle, delay_reason, desc, next_window = self.calculator.calculate_timestamps(
            event_time_utc=event_utc,
            rail=RailType.ACH,
            amount=10000.0,
        )

        self.assertEqual(delay_reason, DelayReason.WEEKEND_NON_WORKING)
        self.assertIn("2nd Saturday Banking Holiday", desc)
        self.assertEqual(next_window.date(), date(2026, 9, 14))

    def test_evaluate_legitimately_delayed_vs_failed(self):
        """
        Evaluate classification:
        1. On-schedule
        2. Legitimately delayed (past cutoff, waiting for next window)
        3. SLA breached (unjustified delay past SLA)
        4. Failed (explicit rejection or max timeout exceeded)
        """
        # Event time: Tuesday Sept 1, 16:30 IST (after ACH 16:00 cutoff)
        event_local = datetime(2026, 9, 1, 16, 30, 0, tzinfo=self.ist)
        event_utc = event_local.astimezone(self.utc)

        txn = PaymentTransaction(
            txn_id="TXN-101",
            rail=RailType.ACH,
            amount=15000.0,
            currency="INR",
            event_time_utc=event_utc,
        )

        # 1. As-of Tuesday 17:00 IST (waiting for tomorrow's settlement window)
        # Expected value time is Wednesday Sept 2 at 09:00 IST.
        as_of_tuesday_utc = datetime(2026, 9, 1, 11, 30, 0, tzinfo=self.utc) # 17:00 IST
        result_delayed = self.calculator.evaluate_transaction(txn, as_of_time_utc=as_of_tuesday_utc)
        self.assertEqual(result_delayed.health, SettlementHealth.LEGITIMATELY_DELAYED)
        self.assertTrue(result_delayed.is_legitimately_delayed)
        self.assertFalse(result_delayed.is_failed)
        self.assertFalse(result_delayed.is_sla_breached)
        self.assertEqual(result_delayed.primary_reason, DelayReason.PAST_DAILY_CUTOFF)

        # 2. As-of Wednesday 13:00 IST (Expected value was 09:00 IST, SLA was 12:00 IST) -> SLA BREACHED
        as_of_wednesday_late_utc = datetime(2026, 9, 2, 7, 30, 0, tzinfo=self.utc) # 13:00 IST
        result_breached = self.calculator.evaluate_transaction(txn, as_of_time_utc=as_of_wednesday_late_utc)
        self.assertEqual(result_breached.health, SettlementHealth.SLA_BREACHED)
        self.assertFalse(result_breached.is_legitimately_delayed)
        self.assertTrue(result_breached.is_sla_breached)
        self.assertFalse(result_breached.is_failed)

        # 3. As-of 5 days later without settlement -> FAILED (timed out beyond max_timeout_hours)
        as_of_5_days_later = datetime(2026, 9, 7, 10, 0, 0, tzinfo=self.utc)
        result_failed_timeout = self.calculator.evaluate_transaction(txn, as_of_time_utc=as_of_5_days_later)
        self.assertEqual(result_failed_timeout.health, SettlementHealth.FAILED)
        self.assertTrue(result_failed_timeout.is_failed)
        self.assertIn("exceeded maximum acceptable rail timeout", result_failed_timeout.reason_description)

        # 4. Explicit Rejection -> FAILED
        txn_rejected = PaymentTransaction(
            txn_id="TXN-102",
            rail=RailType.NEFT,
            amount=5000.0,
            currency="INR",
            event_time_utc=event_utc,
            is_rejected=True,
            rejection_code="BENEFICIARY_ACCOUNT_BLOCKED",
        )
        result_rejected = self.calculator.evaluate_transaction(txn_rejected, as_of_time_utc=as_of_tuesday_utc)
        self.assertEqual(result_rejected.health, SettlementHealth.FAILED)
        self.assertTrue(result_rejected.is_failed)
        self.assertIn("BENEFICIARY_ACCOUNT_BLOCKED", result_rejected.reason_description)


if __name__ == "__main__":
    unittest.main()
