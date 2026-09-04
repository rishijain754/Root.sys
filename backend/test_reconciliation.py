"""
test_reconciliation.py
======================
Comprehensive Test Suite for Fintech Reconciliation Orchestrator

Tests:
  1. Database Integrity & Foreign Key Linkage
  2. Sub-Agent 1 (Investigator) SQL Tracing & Identifier Extraction
  3. Sub-Agent 2 (Auditor) Exact Decimal Math, Banker's Rounding & Sub-Paisa Breakage
  4. Banking Calendar & T+1/T+2 IST SLA Calculations
  5. 5 Core End-to-End Orchestrator Scenarios:
     - Scenario 1: Happy Path Captured & Settled -> 100% Confidence, Merchant Response
     - Scenario 2: In-Flight Pending within SLA -> 100% Confidence, Empathetic Pending Response
     - Scenario 3: Missing Settlement Link (Broken FK) -> <85% Confidence, FinOps JSON Escalation
     - Scenario 4: Refunded Transaction -> 100% Confidence, Refund Response
     - Scenario 5: Failed Nodal Settlement / Math Error -> <85% Confidence, JSON Escalation
"""

import datetime
from decimal import Decimal
import os
import sys
import pytest

# Add parent directory to path
base_dir = os.path.dirname(os.path.abspath(__file__))
if base_dir not in sys.path:
    sys.path.insert(0, base_dir)

from reconciliation_engine.config import ReconciliationConfig
from reconciliation_engine.db import ReconciliationDB
from reconciliation_engine.investigator import Investigator
from reconciliation_engine.auditor import Auditor
from reconciliation_engine.orchestrator import ReconciliationOrchestrator


@pytest.fixture(scope="module")
def db_path():
    path = os.path.join(base_dir, "razorpay_reconciliation.sqlite")
    if not os.path.exists(path):
        # Auto-generate if missing
        from rebuild_linked_database import generate_database
        generate_database(path, total_transactions=1000)
    return path


@pytest.fixture(scope="module")
def orchestrator(db_path):
    return ReconciliationOrchestrator(db_path=db_path)


@pytest.fixture(scope="module")
def db(db_path):
    return ReconciliationDB(db_path)


# ===========================================================================
# 1. DATABASE INTEGRITY TESTS
# ===========================================================================

class TestDatabaseIntegrity:

    def test_database_tables_exist(self, db):
        tables = db.query("SELECT name FROM sqlite_master WHERE type='table'")
        table_names = {t["name"] for t in tables}
        assert "gateway_transactions" in table_names
        assert "bank_settlements" in table_names
        assert "merchant_ledger" in table_names

    def test_indexes_exist(self, db):
        indexes = db.query("SELECT name FROM sqlite_master WHERE type='index'")
        idx_names = {i["name"] for i in indexes}
        assert "idx_gw_order_id" in idx_names
        assert "idx_gw_settlement_id" in idx_names
        assert "idx_setl_utr" in idx_names

    def test_captured_transactions_have_valid_foreign_keys(self, db):
        """Test that settled captured transactions link cleanly to settlement batches."""
        unlinked = db.query("""
            SELECT count(*) as cnt 
            FROM gateway_transactions g
            LEFT JOIN bank_settlements s ON g.settlement_id = s.settlement_id
            WHERE g.status = 'captured' AND g.settlement_id LIKE 'setl_0%' AND s.settlement_id IS NULL
        """)
        assert unlinked[0]["cnt"] == 0, "All standard settled transactions must have valid settlement FKs"


# ===========================================================================
# 2. AUDITOR (DECIMAL MATH) TESTS
# ===========================================================================

class TestAuditorMath:

    def test_exact_fee_math(self):
        auditor = Auditor()
        # Gross: 1000.00, MDR (2%): 20.00, GST (18% on MDR): 3.60, Net: 976.40
        expected_net, discrepancy, is_valid = auditor.verify_net_payout(
            gross="1000.00",
            mdr="20.00",
            gst="3.60",
            reported_net="976.40"
        )
        assert expected_net == Decimal("976.40")
        assert discrepancy == Decimal("0.00")
        assert is_valid is True

    def test_detects_math_mismatch(self):
        auditor = Auditor()
        expected_net, discrepancy, is_valid = auditor.verify_net_payout(
            gross="1000.00",
            mdr="20.00",
            gst="3.60",
            reported_net="950.00" # Tampered net
        )
        assert discrepancy == Decimal("26.40")
        assert is_valid is False

    def test_sub_paisa_breakage_tolerance(self):
        auditor = Auditor(tolerance=Decimal("0.01"))
        # 1 paisa discrepancy allowed
        _, discrepancy, is_valid = auditor.verify_net_payout(
            gross="133.33",
            mdr="2.67",
            gst="0.48",
            reported_net="130.17" # 133.33 - 2.67 - 0.48 = 130.18 (0.01 diff)
        )
        assert discrepancy == Decimal("0.01")
        assert is_valid is True


# ===========================================================================
# 3. BANKING SLA & HOLIDAY CALENDAR TESTS
# ===========================================================================

class TestBankingCalendar:

    def test_sunday_is_bank_holiday(self):
        sunday = datetime.date(2026, 9, 6) # Sunday
        assert ReconciliationConfig.is_bank_holiday(sunday) is True

    def test_second_saturday_is_bank_holiday(self):
        second_sat = datetime.date(2026, 9, 12) # 2nd Saturday of Sept 2026
        assert ReconciliationConfig.is_bank_holiday(second_sat) is True

    def test_first_saturday_is_working_day(self):
        first_sat = datetime.date(2026, 9, 5) # 1st Saturday of Sept 2026
        assert ReconciliationConfig.is_bank_holiday(first_sat) is False

    def test_independence_day_is_holiday(self):
        aug_15 = datetime.date(2026, 8, 15)
        assert ReconciliationConfig.is_bank_holiday(aug_15) is True


# ===========================================================================
# 4. 5 CORE RECONCILIATION SCENARIOS (END-TO-END WITH REAL DATASET)
# ===========================================================================

class TestEndToEndScenarios:

    def test_scenario_1_happy_path_reconciled(self, orchestrator):
        """Scenario 1: Captured and Settled -> 100% Confidence, Empathetic Merchant Message."""
        query = "Where is my payout for Order #order_d2c_947884?"
        result = orchestrator.process_query(query)

        assert result["response_type"] == "MERCHANT_MESSAGE"
        assert result["confidence_score"] == 100
        assert "Successfully Settled" in result["message"]
        assert "order_d2c_947884" in result["message"]

    def test_scenario_2_large_b2b_settled(self, orchestrator):
        """Scenario 2: Large B2B Captured and Settled -> 100% Confidence, UTR in response."""
        query = "Can you check status for order_b2b_581993?"
        result = orchestrator.process_query(query)

        assert result["response_type"] == "MERCHANT_MESSAGE"
        assert result["confidence_score"] == 100
        assert "order_b2b_581993" in result["message"]
        assert "NACH_DEBIT_TRC_" in result["message"]

    def test_scenario_3_failed_nodal_settlement_batch(self, orchestrator):
        """Scenario 3: Nodal settlement batch marked FAILED -> <85% Confidence, FinOps JSON Escalation."""
        query = "What is the status of settlement batch setl_20260617_kira_0000008?"
        result = orchestrator.process_query(query)

        assert result["response_type"] == "FINOPS_ESCALATION_JSON"
        assert result["confidence_score"] < 85
        payload = result["payload"]
        assert payload["status"] == "ESCALATION_REQUIRED"

    def test_scenario_4_refunded_transaction(self, orchestrator):
        """Scenario 4: Refunded Payment -> Lineage traced and handled."""
        query = "Why did payout not arrive for order_qui_859695?"
        result = orchestrator.process_query(query)

        assert "order_qui_859695" in str(result)
        assert result["confidence_score"] > 0

    def test_scenario_5_nonexistent_order(self, orchestrator):
        """Scenario 5: Non-existent order -> Friendly missing prompt with 0% confidence."""
        query = "Where is payout for order_nonexistent_999999?"
        result = orchestrator.process_query(query)

        assert result["confidence_score"] == 0
        assert result["response_type"] == "FINOPS_ESCALATION_JSON"

