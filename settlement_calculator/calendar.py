"""
Banking calendar implementation with timezone normalization, Indian banking rules
(2nd and 4th Saturday holidays, Sundays, RBI public holidays), and next business day resolvers.
"""

from datetime import date, datetime, time, timedelta
from typing import Dict, Optional, Set, Tuple
import zoneinfo


class BankingCalendar:
    """
    Calendar normalization for financial settlement systems.
    Encapsulates timezone conversion and banking holiday rules.
    """

    def __init__(
        self,
        timezone_name: str = "Asia/Kolkata",
        custom_holidays: Optional[Dict[date, str]] = None
    ):
        self.timezone_name = timezone_name
        self.tz = zoneinfo.ZoneInfo(timezone_name)
        
        # Standard recurring and declared Indian Banking Holidays
        self.holidays: Dict[date, str] = {
            # 2026 Sample Calendar
            date(2026, 1, 26): "Republic Day",
            date(2026, 4, 1): "RBI Annual Bank Accounts Closing",
            date(2026, 4, 3): "Good Friday",
            date(2026, 4, 14): "Dr. Ambedkar Jayanti",
            date(2026, 5, 1): "Maharashtra Day / May Day",
            date(2026, 8, 15): "Independence Day",
            date(2026, 10, 2): "Mahatma Gandhi Jayanti",
            date(2026, 10, 20): "Dussehra / Vijaya Dashami",
            date(2026, 11, 8): "Diwali / Laxmi Pujan",
            date(2026, 11, 9): "Diwali Balipratipada",
            date(2026, 12, 25): "Christmas",
            
            # 2025 Calendar for historical tests
            date(2025, 1, 26): "Republic Day",
            date(2025, 4, 1): "RBI Annual Bank Accounts Closing",
            date(2025, 8, 15): "Independence Day",
            date(2025, 10, 2): "Mahatma Gandhi Jayanti",
            date(2025, 12, 25): "Christmas",
        }
        
        if custom_holidays:
            self.holidays.update(custom_holidays)

    def to_local(self, dt: datetime) -> datetime:
        """Ensure datetime is converted to the local banking timezone."""
        if dt.tzinfo is None:
            # If naive, assume UTC and localize
            dt = dt.replace(tzinfo=zoneinfo.ZoneInfo("UTC"))
        return dt.astimezone(self.tz)

    def to_utc(self, dt: datetime) -> datetime:
        """Convert any timezone-aware datetime to UTC."""
        if dt.tzinfo is None:
            # If naive, localize to rail's local timezone first
            dt = dt.replace(tzinfo=self.tz)
        return dt.astimezone(zoneinfo.ZoneInfo("UTC"))

    def get_saturday_occurrence(self, d: date) -> Optional[int]:
        """
        Returns the ordinal Saturday occurrence in the month (1st, 2nd, 3rd, 4th, 5th),
        or None if the day is not a Saturday.
        """
        if d.weekday() != 5:  # 5 = Saturday
            return None
        return (d.day - 1) // 7 + 1

    def is_second_or_fourth_saturday(self, d: date) -> bool:
        """
        In Indian Banking (RBI circular DBOD.No.Leg.BC.31/09.07.005/2015-16),
        the 2nd and 4th Saturdays of every month are public holidays.
        The 1st, 3rd, and 5th Saturdays are regular working days.
        """
        nth = self.get_saturday_occurrence(d)
        return nth in (2, 4)

    def is_banking_day(self, d: date) -> Tuple[bool, Optional[str]]:
        """
        Determine if the given date is an active banking day.
        Returns: (is_working_day, reason_if_holiday)
        """
        # 1. Sundays are always holidays
        if d.weekday() == 6:
            return False, "Sunday Weekend Holiday"

        # 2. 2nd & 4th Saturdays are holidays
        if d.weekday() == 5:
            nth = self.get_saturday_occurrence(d)
            if nth in (2, 4):
                suffix = "nd" if nth == 2 else "th"
                return False, f"{nth}{suffix} Saturday Banking Holiday"
            # 1st, 3rd, 5th Saturdays are working days
            # Still need to check if there is an explicit public holiday on this Saturday

        # 3. Check public / RBI holidays
        if d in self.holidays:
            return False, f"Public Holiday: {self.holidays[d]}"

        return True, None

    def get_next_banking_day(self, start_date: date) -> Tuple[date, str]:
        """
        Advance forward day by day until the next active banking day is found.
        Returns: (next_banking_date, reason_for_skipping_any_days)
        """
        curr = start_date + timedelta(days=1)
        skipped_reasons = []
        
        while True:
            is_working, reason = self.is_banking_day(curr)
            if is_working:
                break
            skipped_reasons.append(f"{curr.isoformat()} ({reason})")
            curr += timedelta(days=1)
            
        summary = "; ".join(skipped_reasons) if skipped_reasons else "Next calendar day"
        return curr, summary

    def add_holiday(self, holiday_date: date, description: str) -> None:
        """Register an ad-hoc or regional bank holiday."""
        self.holidays[holiday_date] = description
