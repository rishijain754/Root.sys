"""
Interactive Demonstration Script: Expected Settlement Calculator & Timestamps
Runs through realistic real-world banking scenarios displaying:
- UTC to Local Banking Timezone normalization
- Event Time vs Booking Time vs Value Time
- Cutoffs, 2nd/4th Saturday Indian banking holidays, and NEFT/RTGS schedules
- Transaction health evaluation (On-Schedule, Legitimately Delayed, SLA Breached, Failed)
"""

from datetime import datetime, timezone, timedelta
import json
import zoneinfo

import os
import sys
from pathlib import Path

# Add project root to sys.path so demo can be executed directly or as a module
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from settlement_calculator.models import (
    RailType,
    PaymentTransaction,
    SettlementHealth,
)
from settlement_calculator.calendar import BankingCalendar
from settlement_calculator.calculator import ExpectedSettlementCalculator


def print_separator(title: str):
    print("\n" + "=" * 90)
    print(f"  {title.upper()}")
    print("=" * 90)


def display_scenario_result(scenario_name: str, tx: PaymentTransaction, as_of_utc: datetime, calculator: ExpectedSettlementCalculator):
    result = calculator.evaluate_transaction(tx, as_of_time_utc=as_of_utc)
    lc = result.lifecycle

    print(f"\n[SCENARIO] {scenario_name}")
    print(f"Transaction ID  : {tx.txn_id}")
    print(f"Rail            : {tx.rail.value}")
    print(f"Amount          : INR {tx.amount:,.2f}")
    print("-" * 90)
    print("TIMESTAMP LIFECYCLE BREAKDOWN:")
    print(f"  1. Event Time (Authorized)   : UTC: {lc.event_time_utc.strftime('%Y-%m-%d %H:%M:%S %Z')} | Local: {lc.event_time_local.strftime('%Y-%m-%d %H:%M:%S %Z')}")
    print(f"  2. Booking Time (Ledger)     : UTC: {lc.booking_time_utc.strftime('%Y-%m-%d %H:%M:%S %Z')} | Local: {lc.booking_time_local.strftime('%Y-%m-%d %H:%M:%S %Z')}")
    print(f"  3. Expected Value (Cleared)  : UTC: {lc.expected_value_time_utc.strftime('%Y-%m-%d %H:%M:%S %Z')} | Local: {lc.expected_value_time_local.strftime('%Y-%m-%d %H:%M:%S %Z')}")
    print(f"  4. SLA Cutoff Deadline       : UTC: {lc.sla_deadline_utc.strftime('%Y-%m-%d %H:%M:%S %Z')} | Local: {lc.sla_deadline_local.strftime('%Y-%m-%d %H:%M:%S %Z')}")
    if lc.cutoff_time_local:
        print(f"     Daily Cutoff Time         : Local: {lc.cutoff_time_local.strftime('%Y-%m-%d %H:%M:%S %Z')}")
    print("-" * 90)
    print(f"Observation Time (As-Of)      : Local: {calculator.calendar.to_local(as_of_utc).strftime('%Y-%m-%d %H:%M:%S %Z')}")
    
    # Status coloring
    status = result.health.value
    print(f"Settlement Health             : >>> {status} <<<")
    print(f"Legitimately Delayed?         : {'YES' if result.is_legitimately_delayed else 'NO'}")
    print(f"SLA Breached?                 : {'YES' if result.is_sla_breached else 'NO'}")
    print(f"Failed?                       : {'YES' if result.is_failed else 'NO'}")
    print(f"Primary Delay Reason          : {result.primary_reason.value}")
    print(f"Explanation                   : {result.reason_description}")
    if result.next_clearing_window_local:
        print(f"Next Clearing Window          : {result.next_clearing_window_local.strftime('%Y-%m-%d %H:%M:%S %Z')}")


def main():
    utc = zoneinfo.ZoneInfo("UTC")
    ist = zoneinfo.ZoneInfo("Asia/Kolkata")
    calendar = BankingCalendar(timezone_name="Asia/Kolkata")
    calculator = ExpectedSettlementCalculator(calendar=calendar)

    print_separator("Expected Settlement Calculator & Timestamp Normalization Engine")
    print("Timezone Engine: UTC <-> Asia/Kolkata (IST)")
    print("Banking Calendar: Indian Banking (2nd & 4th Saturdays, Sundays, Public Holidays)")

    # -------------------------------------------------------------
    # Scenario 1: Standard NEFT batch processing on a business day
    # -------------------------------------------------------------
    tx1_event = datetime(2026, 9, 2, 8, 42, 0, tzinfo=utc)  # 14:12 IST, Wednesday
    tx1 = PaymentTransaction(
        txn_id="TXN-NEFT-001",
        rail=RailType.NEFT,
        amount=45000.0,
        currency="INR",
        event_time_utc=tx1_event,
    )
    # Observed 3 minutes later (14:15 IST)
    as_of_1 = tx1_event + timedelta(minutes=3)
    display_scenario_result(
        "Scenario 1: NEFT Transaction in-flight during half-hourly batch window",
        tx1,
        as_of_1,
        calculator
    )

    # -------------------------------------------------------------
    # Scenario 2: Weekend 2nd Saturday Holiday Rollover
    # -------------------------------------------------------------
    # Sept 12, 2026 is 2nd Saturday
    tx2_event = datetime(2026, 9, 12, 4, 30, 0, tzinfo=utc)  # 10:00 IST, 2nd Saturday
    tx2 = PaymentTransaction(
        txn_id="TXN-ACH-002",
        rail=RailType.ACH,
        amount=120000.0,
        currency="INR",
        event_time_utc=tx2_event,
    )
    # Observed on Sunday afternoon
    as_of_2 = datetime(2026, 9, 13, 8, 0, 0, tzinfo=utc)  # 13:30 IST Sunday
    display_scenario_result(
        "Scenario 2: Transaction initiated on 2nd Saturday Banking Holiday",
        tx2,
        as_of_2,
        calculator
    )

    # -------------------------------------------------------------
    # Scenario 3: Post-Cutoff Transaction (Legitimately Delayed)
    # -------------------------------------------------------------
    # Tuesday Sept 1, 16:45 IST (ACH Cutoff is 16:00 IST)
    tx3_event = datetime(2026, 9, 1, 11, 15, 0, tzinfo=utc)  # 16:45 IST
    tx3 = PaymentTransaction(
        txn_id="TXN-ACH-003",
        rail=RailType.ACH,
        amount=85000.0,
        currency="INR",
        event_time_utc=tx3_event,
    )
    # Observed Tuesday evening 18:00 IST
    as_of_3 = datetime(2026, 9, 1, 12, 30, 0, tzinfo=utc)
    display_scenario_result(
        "Scenario 3: Transaction submitted past daily cutoff time",
        tx3,
        as_of_3,
        calculator
    )

    # -------------------------------------------------------------
    # Scenario 4: SLA Breached (Unjustified Interbank Delay)
    # -------------------------------------------------------------
    # Expected value time Wednesday 09:00 IST, SLA was 12:00 IST.
    # Observed Wednesday 14:30 IST without confirmation.
    as_of_4 = datetime(2026, 9, 2, 9, 0, 0, tzinfo=utc)  # 14:30 IST Wednesday
    display_scenario_result(
        "Scenario 4: SLA Breached (Missed Expected Value Window without confirmation)",
        tx3,
        as_of_4,
        calculator
    )

    # -------------------------------------------------------------
    # Scenario 5: RTGS High-Value vs Below-Threshold
    # -------------------------------------------------------------
    tx5_event = datetime(2026, 9, 3, 5, 0, 0, tzinfo=utc)  # 10:30 IST Thursday
    tx5_valid = PaymentTransaction(
        txn_id="TXN-RTGS-005A",
        rail=RailType.RTGS,
        amount=5000000.0,  # 50 Lakhs (valid)
        currency="INR",
        event_time_utc=tx5_event,
    )
    display_scenario_result(
        "Scenario 5A: RTGS High-Value Gross Real-Time Settlement",
        tx5_valid,
        tx5_event + timedelta(minutes=5),
        calculator
    )

    tx5_invalid = PaymentTransaction(
        txn_id="TXN-RTGS-005B",
        rail=RailType.RTGS,
        amount=50000.0,  # 50k (under 2 Lakhs minimum)
        currency="INR",
        event_time_utc=tx5_event,
    )
    display_scenario_result(
        "Scenario 5B: RTGS Below Minimum Amount Threshold",
        tx5_invalid,
        tx5_event + timedelta(minutes=5),
        calculator
    )

    # -------------------------------------------------------------
    # Scenario 6: Explicit Rejection / Failed
    # -------------------------------------------------------------
    tx6 = PaymentTransaction(
        txn_id="TXN-NEFT-006",
        rail=RailType.NEFT,
        amount=15000.0,
        currency="INR",
        event_time_utc=tx1_event,
        is_rejected=True,
        rejection_code="BENEFICIARY_ACCOUNT_FROZEN",
    )
    display_scenario_result(
        "Scenario 6: Beneficiary Rail Rejection (Technical / Compliance Failure)",
        tx6,
        as_of_1,
        calculator
    )


if __name__ == "__main__":
    main()
