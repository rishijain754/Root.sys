"""
Domain models, enums, and data structures for Expected Settlement Calculator.
Distinguishes between:
- Event Time (T_event): Transaction authorization / initiation.
- Booking Time (T_booking): Core ledger posting.
- Value Time (T_value): Bank funds legally available / interbank clearance.
"""

from dataclasses import dataclass, field
from datetime import datetime, time
from enum import Enum
from typing import Optional, Dict, Any, List


class RailType(str, Enum):
    """Interbank settlement rails."""
    NEFT = "NEFT"       # National Electronic Funds Transfer (India: 48 half-hourly batches)
    RTGS = "RTGS"       # Real Time Gross Settlement (India: Gross real-time settlement)
    IMPS = "IMPS"       # Immediate Payment Service (India: 24x7 instant)
    UPI = "UPI"         # Unified Payments Interface (24x7 instant)
    ACH = "ACH"         # Automated Clearing House / NACH (batch next-day)


class SettlementHealth(str, Enum):
    """Health classification of transaction settlement."""
    ON_SCHEDULE = "ON_SCHEDULE"                     # In-flight within standard clearing window
    SETTLED = "SETTLED"                             # Successfully credited and value realized
    LEGITIMATELY_DELAYED = "LEGITIMATELY_DELAYED"   # Delayed due to banking rules (cutoff, weekend, holiday)
    SLA_BREACHED = "SLA_BREACHED"                   # Missed expected value time window without confirmation
    FAILED = "FAILED"                               # Explicit rejection, NACK, or fatal timeout


class DelayReason(str, Enum):
    """Categorized root causes for legitimate settlement delays."""
    NONE = "NONE"
    PAST_DAILY_CUTOFF = "PAST_DAILY_CUTOFF"                   # Initiated after daily rail cutoff time
    WEEKEND_NON_WORKING = "WEEKEND_NON_WORKING"               # Sunday or 2nd/4th Saturday banking closure
    PUBLIC_BANK_HOLIDAY = "PUBLIC_BANK_HOLIDAY"               # Designated banking/clearing holiday
    BATCH_CYCLE_WAIT = "BATCH_CYCLE_WAIT"                     # Awaiting next batch window (e.g. NEFT half-hour)
    OUTSIDE_OPERATING_HOURS = "OUTSIDE_OPERATING_HOURS"       # Rail is closed during night hours (e.g. legacy/ACH)
    AMOUNT_THRESHOLD_VIOLATION = "AMOUNT_THRESHOLD_VIOLATION" # e.g. RTGS below minimum amount threshold


@dataclass(frozen=True)
class RailScheduleConfig:
    """Configuration for banking rails, cutoffs, and SLA rules."""
    rail: RailType
    timezone_name: str = "Asia/Kolkata"
    
    # Operating window (Local rail time)
    start_time: time = time(0, 0)       # e.g. 00:00 for 24x7 NEFT/RTGS, or 08:00
    cutoff_time: Optional[time] = None  # None for pure 24x7 instant, or e.g. 17:30 / 23:00 for EOD cutoff
    
    # Clearing behavior
    is_24x7: bool = True
    batch_interval_minutes: Optional[int] = None  # e.g. 30 for NEFT, None for continuous RTGS
    instant_settlement: bool = False               # e.g. UPI/IMPS
    
    # Amount limits (e.g. RTGS minimum INR 2,00,000)
    min_amount: Optional[float] = None
    max_amount: Optional[float] = None
    
    # Typical time offsets in seconds
    expected_booking_lag_seconds: int = 2          # Time between event auth and ledger booking
    expected_settlement_lag_minutes: int = 15      # Processing / interbank transmission lag
    grace_period_minutes: int = 30                 # Tolerance beyond expected value time before SLA breach
    max_timeout_hours: int = 48                    # Beyond which an unconfirmed txn is declared failed


@dataclass
class TimestampLifecycle:
    """
    Distinguishes the three fundamental timestamps in financial transactions:
    1. Event Time: When transaction was initiated/authorized.
    2. Booking Time: When core ledger was posted/debited.
    3. Value Time: When cleared funds are available to beneficiary.
    """
    # UTC timestamps (standardized storage & cross-system exchange)
    event_time_utc: datetime
    booking_time_utc: datetime
    expected_value_time_utc: datetime
    sla_deadline_utc: datetime
    cutoff_time_utc: Optional[datetime] = None
    actual_value_time_utc: Optional[datetime] = None

    # Local banking zone timestamps (for human audit & bank cutoff reconciliation)
    timezone_name: str = "Asia/Kolkata"
    event_time_local: Optional[datetime] = None
    booking_time_local: Optional[datetime] = None
    expected_value_time_local: Optional[datetime] = None
    sla_deadline_local: Optional[datetime] = None
    cutoff_time_local: Optional[datetime] = None
    actual_value_time_local: Optional[datetime] = None

    def to_dict(self) -> Dict[str, Any]:
        """Convert timestamps to ISO-8601 serializable dict."""
        return {
            "timezone": self.timezone_name,
            "utc": {
                "event_time": self.event_time_utc.isoformat(),
                "booking_time": self.booking_time_utc.isoformat(),
                "expected_value_time": self.expected_value_time_utc.isoformat(),
                "sla_deadline": self.sla_deadline_utc.isoformat(),
                "cutoff_time": self.cutoff_time_utc.isoformat() if self.cutoff_time_utc else None,
                "actual_value_time": self.actual_value_time_utc.isoformat() if self.actual_value_time_utc else None,
            },
            "local": {
                "event_time": self.event_time_local.isoformat() if self.event_time_local else None,
                "booking_time": self.booking_time_local.isoformat() if self.booking_time_local else None,
                "expected_value_time": self.expected_value_time_local.isoformat() if self.expected_value_time_local else None,
                "sla_deadline": self.sla_deadline_local.isoformat() if self.sla_deadline_local else None,
                "cutoff_time": self.cutoff_time_local.isoformat() if self.cutoff_time_local else None,
                "actual_value_time": self.actual_value_time_local.isoformat() if self.actual_value_time_local else None,
            }
        }


@dataclass
class PaymentTransaction:
    """Represents a transaction event to be evaluated."""
    txn_id: str
    rail: RailType
    amount: float
    currency: str
    event_time_utc: datetime
    booking_time_utc: Optional[datetime] = None
    actual_value_time_utc: Optional[datetime] = None
    is_rejected: bool = False
    rejection_code: Optional[str] = None


@dataclass
class EvaluationResult:
    """Outcome of evaluating transaction settlement timeline and health."""
    txn_id: str
    rail: RailType
    health: SettlementHealth
    is_legitimately_delayed: bool
    is_sla_breached: bool
    is_failed: bool
    primary_reason: DelayReason
    reason_description: str
    lifecycle: TimestampLifecycle
    next_clearing_window_local: Optional[datetime] = None
    metadata: Dict[str, Any] = field(default_factory=dict)
