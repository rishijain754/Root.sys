"""
config.py
=========
Fintech Reconciliation Engine — Configuration & Banking SLA Rules

Handles:
  - Database file location resolution
  - Timezone-aware IST cutoff calculations (T+1, T+2, T+3 settlement SLAs)
  - Indian Bank Holiday Calendar (2026) including 2nd/4th Saturdays and Sundays
  - Confidence scoring thresholds and discrepancy tolerances
"""

import datetime
from decimal import Decimal
import os
from typing import Optional

# ---------------------------------------------------------------------------
# Default Constants
# ---------------------------------------------------------------------------
DEFAULT_DB_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "razorpay_reconciliation.sqlite"
)

# Settlement Cutoff Time in IST (18:00 = 6:00 PM)
DAILY_CUTOFF_HOUR = 18

# Default SLA: T+2 business days for standard domestic payment gateway settlement
DEFAULT_SLA_DAYS = 2

# Maximum allowed rounding/sub-paisa discrepancy tolerance (in INR)
MAX_BREAKAGE_TOLERANCE = Decimal("0.01")

# Confidence Score Thresholds
CONFIDENCE_FULL = 100
CONFIDENCE_ESCALATION_MAX = 84

# ---------------------------------------------------------------------------
# Indian Bank Holidays Calendar (2026) - RBI Schedule
# ---------------------------------------------------------------------------
INDIAN_BANK_HOLIDAYS_2026 = {
    datetime.date(2026, 1, 26): "Republic Day",
    datetime.date(2026, 3, 3): "Holi",
    datetime.date(2026, 3, 20): "Id-ul-Fitr (Ramadan Eid)",
    datetime.date(2026, 4, 1): "Bank Annual Accounts Closing",
    datetime.date(2026, 4, 3): "Good Friday",
    datetime.date(2026, 4, 14): "Dr. B.R. Ambedkar Jayanti",
    datetime.date(2026, 5, 1): "Maharashtra Day / May Day",
    datetime.date(2026, 5, 27): "Bakrid / Eid al-Adha",
    datetime.date(2026, 8, 15): "Independence Day",
    datetime.date(2026, 8, 27): "Raksha Bandhan",
    datetime.date(2026, 9, 4): "Janmashtami",
    datetime.date(2026, 10, 2): "Mahatma Gandhi Jayanti",
    datetime.date(2026, 10, 20): "Dussehra (Vijayadashami)",
    datetime.date(2026, 11, 8): "Diwali (Laxmi Pujan)",
    datetime.date(2026, 11, 9): "Diwali Balipratipada",
    datetime.date(2026, 11, 24): "Guru Nanak Jayanti",
    datetime.date(2026, 12, 25): "Christmas",
}


class ReconciliationConfig:
    """Configuration and SLA Calculation Engine for Payment Reconciliation."""

    def __init__(
        self,
        db_path: Optional[str] = None,
        cutoff_hour: int = DAILY_CUTOFF_HOUR,
        sla_days: int = DEFAULT_SLA_DAYS,
        tolerance: Decimal = MAX_BREAKAGE_TOLERANCE,
    ):
        self.db_path = db_path or os.environ.get("RECONCILIATION_DB_PATH", DEFAULT_DB_PATH)
        self.cutoff_hour = cutoff_hour
        self.sla_days = sla_days
        self.tolerance = tolerance

    @staticmethod
    def is_bank_holiday(date_obj: datetime.date) -> bool:
        """
        Check if a given date is an Indian Banking Holiday:
          - Every Sunday
          - 2nd and 4th Saturday of the month
          - RBI National Holidays
        """
        # Sundays are always holidays
        if date_obj.weekday() == 6:
            return True

        # Saturdays: 2nd and 4th Saturday are bank holidays
        if date_obj.weekday() == 5:
            day = date_obj.day
            # 1st week: 1-7, 2nd week: 8-14, 3rd: 15-21, 4th: 22-28, 5th: 29-31
            if 8 <= day <= 14 or 22 <= day <= 28:
                return True

        # RBI Calendar Holidays
        if date_obj in INDIAN_BANK_HOLIDAYS_2026:
            return True

        return False

    def get_next_business_day(self, start_date: datetime.date) -> datetime.date:
        """Find the immediately following Indian banking business day."""
        curr = start_date + datetime.timedelta(days=1)
        while self.is_bank_holiday(curr):
            curr += datetime.timedelta(days=1)
        return curr

    def calculate_expected_settlement_date(
        self,
        captured_timestamp: datetime.datetime,
        sla_business_days: Optional[int] = None
    ) -> datetime.date:
        """
        Calculate expected bank credit date using IST T+N rules:
          - If captured after cutoff_hour (18:00 IST), Day T starts next business day.
          - Advances by N banking business days skipping weekends & bank holidays.
        """
        sla_days = sla_business_days if sla_business_days is not None else self.sla_days

        # Determine effective Day T
        captured_date = captured_timestamp.date()
        if captured_timestamp.hour >= self.cutoff_hour or self.is_bank_holiday(captured_date):
            day_t = self.get_next_business_day(captured_date)
        else:
            day_t = captured_date

        # Advance by N business days
        expected_date = day_t
        for _ in range(sla_days):
            expected_date = self.get_next_business_day(expected_date)

        return expected_date

    def is_within_sla(
        self,
        captured_timestamp: datetime.datetime,
        current_time: Optional[datetime.datetime] = None
    ) -> tuple[bool, datetime.date]:
        """
        Check if an unsettled transaction is still within normal settlement SLA.
        Returns (is_within_sla, expected_settlement_date).
        """
        now = current_time or datetime.datetime.now()
        expected_date = self.calculate_expected_settlement_date(captured_timestamp)
        is_within = now.date() <= expected_date
        return is_within, expected_date
