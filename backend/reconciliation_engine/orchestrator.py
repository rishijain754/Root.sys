"""
orchestrator.py
===============
Fintech Reconciliation Engine — Master Coordinator

Coordinates the Investigator (Sub-Agent 1) and Auditor (Sub-Agent 2) to process natural-language
merchant queries, compute confidence scores, and return empathetic merchant explanations (100% confidence)
or structured FinOps JSON escalation payloads (<85% confidence).
"""

import datetime
from decimal import Decimal
import json
import re
from typing import Any, Optional

from .config import ReconciliationConfig, CONFIDENCE_FULL, CONFIDENCE_ESCALATION_MAX
from .db import ReconciliationDB
from .investigator import Investigator, InvestigationResult
from .auditor import Auditor, AuditResult


class ReconciliationOrchestrator:
    """
    Master coordinator for fintech reconciliation.
    Coordinates query parsing -> investigative SQL -> AST decimal math -> confidence & response generation.
    """

    # Identifier extraction regex patterns
    PATTERNS = {
        "order_id": re.compile(r"\b(order_[a-zA-Z0-9_\-]+|ORD_[a-zA-Z0-9_\-]+)\b", re.IGNORECASE),
        "payment_id": re.compile(r"\b(pay_[a-zA-Z0-9_\-]+|TXN_[a-zA-Z0-9_\-]+)\b", re.IGNORECASE),
        "settlement_id": re.compile(r"\b(setl_[a-zA-Z0-9_\-]+|BATCH_[a-zA-Z0-9_\-]+)\b", re.IGNORECASE),
        "utr_number": re.compile(r"\b(UTR_[a-zA-Z0-9_\-]+)\b", re.IGNORECASE),
        "merchant_id": re.compile(r"\b(acc_[a-zA-Z0-9_\-]+|MERCH_[a-zA-Z0-9_\-]+)\b", re.IGNORECASE),
    }

    def __init__(self, db_path: Optional[str] = None, config: Optional[ReconciliationConfig] = None):
        self.config = config or ReconciliationConfig(db_path=db_path)
        self.db = ReconciliationDB(self.config.db_path)
        self.investigator = Investigator(self.db)
        self.auditor = Auditor(tolerance=self.config.tolerance)

    def extract_identifiers(self, query_text: str) -> dict[str, str]:
        """Extract payment/order/settlement identifiers from free-form merchant query text."""
        extracted = {}
        for key, pattern in self.PATTERNS.items():
            match = pattern.search(query_text)
            if match:
                extracted[key] = match.group(1)
        return extracted

    def process_query(
        self,
        query_text: str,
        simulated_current_time: Optional[datetime.datetime] = None
    ) -> dict[str, Any]:
        """
        Main entry point for resolving a merchant query.
        Returns a dictionary containing response_type ('MERCHANT_MESSAGE' or 'FINOPS_ESCALATION_JSON'),
        confidence_score, and the formatted payload.
        """
        curr_time = simulated_current_time or datetime.datetime(2026, 9, 4, 18, 0, 0)
        
        # Check for human handoff intent first
        query_lower = query_text.lower()
        handoff_keywords = ["human", "agent", "real person", "talk to", "speak to", "contact support", "connect me", "customer care", "support team"]
        if any(kw in query_lower for kw in handoff_keywords):
            return {
                "response_type": "HUMAN_HANDOFF",
                "confidence_score": 100,
                "message": (
                    "I understand you'd like to speak with a human support agent. "
                    "I'm connecting you to our FinOps support team who can assist you further."
                )
            }

        ids = self.extract_identifiers(query_text)

        # Step 1: Query Execution via Investigator
        if "order_id" in ids:
            inv = self.investigator.investigate_by_order_id(ids["order_id"])
        elif "payment_id" in ids:
            inv = self.investigator.investigate_by_payment_id(ids["payment_id"])
        elif "settlement_id" in ids:
            inv = self.investigator.investigate_by_settlement_id(ids["settlement_id"])
        elif "utr_number" in ids:
            setl = self.db.get_settlement_by_utr(ids["utr_number"])
            if setl:
                inv = self.investigator.investigate_by_settlement_id(setl["settlement_id"])
            else:
                inv = InvestigationResult(
                    query_type="utr_number",
                    query_value=ids["utr_number"],
                    missing_links=["UTR_NOT_FOUND"],
                    lifecycle_stage="NOT_FOUND"
                )
        else:
            return {
                "response_type": "MERCHANT_MESSAGE",
                "confidence_score": 0,
                "message": (
                    "Hello! We could not locate a valid Order ID (e.g., order_d2c_123456) or "
                    "Payment ID (e.g., pay_0000000165) in your query. Please provide your transaction details "
                    "so we can check the status of your settlement."
                )
            }

        # Step 2: Mathematical Audit via Auditor
        audit = self.auditor.audit(inv)

        # Step 3: SLA & Timeline Analysis
        is_pending_within_sla = False
        expected_settle_date = None

        if inv.gateway_records:
            primary_gw = inv.gateway_records[0]
            captured_at_str = primary_gw.get("captured_at") or primary_gw.get("created_at")
            if captured_at_str:
                try:
                    captured_dt = datetime.datetime.strptime(captured_at_str, "%Y-%m-%d %H:%M:%S")
                    is_within, expected_date = self.config.is_within_sla(captured_dt, curr_time)
                    expected_settle_date = expected_date
                    if is_within and inv.lifecycle_stage in ["CAPTURED", "BATCHED"]:
                        is_pending_within_sla = True
                except ValueError:
                    pass

        # Step 4: Confidence Scoring
        confidence = self._compute_confidence(inv, audit, is_pending_within_sla)

        # Step 5: Response Generation (100% vs <85%)
        if confidence == CONFIDENCE_FULL:
            response_text = self._generate_merchant_response(inv, audit, is_pending_within_sla, expected_settle_date)
            return {
                "response_type": "MERCHANT_MESSAGE",
                "confidence_score": confidence,
                "lifecycle_stage": inv.lifecycle_stage,
                "message": response_text,
                "audit_summary": audit.verification_details,
            }
        else:
            escalation_payload = self._generate_escalation_json(inv, audit, confidence, is_pending_within_sla)
            return {
                "response_type": "FINOPS_ESCALATION_JSON",
                "confidence_score": confidence,
                "lifecycle_stage": inv.lifecycle_stage,
                "payload": escalation_payload,
            }

    def _compute_confidence(
        self,
        inv: InvestigationResult,
        audit: AuditResult,
        is_pending_within_sla: bool
    ) -> int:
        """
        Compute confidence score:
          100%: Complete 3-way link with exact math OR captured within SLA with exact math.
          <85%: Broken links, math discrepancies, failed payments, or overdue in-flight payments.
        """
        if inv.lifecycle_stage == "NOT_FOUND":
            return 0

        if inv.is_failed or "GATEWAY_PAYMENT_FAILED" in inv.missing_links:
            return 100 # Confidently identified as failed at gateway

        if inv.is_refunded:
            return 100 # Fully tracked refund flow

        if not audit.math_verified or audit.discrepancy_payout > Decimal("0.00"):
            return 60 # Math mismatch detected

        if inv.lifecycle_stage == "SETTLED" and not inv.missing_links:
            return 100 # Fully settled happy path

        if is_pending_within_sla and inv.lifecycle_stage in ["CAPTURED", "BATCHED"]:
            return 100 # Normal pending within T+1/T+2 banking SLA

        # Any broken FK or overdue missing settlement
        if "BROKEN_SETTLEMENT_FK" in inv.missing_links:
            return 45
        if "SETTLEMENT_BATCH_FAILED" in inv.missing_links:
            return 50
        if "UNBATCHED_SETTLEMENT" in inv.missing_links:
            return 65

        return 70

    def _generate_merchant_response(
        self,
        inv: InvestigationResult,
        audit: AuditResult,
        is_pending_within_sla: bool,
        expected_settle_date: Optional[datetime.date]
    ) -> str:
        """Generate empathetic, transparent, human-readable merchant response."""
        gw = inv.gateway_records[0]
        order_id = gw.get("order_id", "N/A")
        payment_id = gw.get("payment_id", "N/A")
        gross = audit.gross_amount
        net = audit.reported_net
        mdr = audit.mdr_fee
        gst = audit.gst

        if inv.is_failed:
            return (
                f"Hello! Regarding your inquiry for Order #{order_id} (Payment ID: {payment_id}):\n\n"
                f"The customer's payment attempt of INR {gross:,.2f} unfortunately failed at the bank/gateway level "
                f"on {gw.get('created_at')}. No funds were debited from your customer or credited to your Razorpay balance. "
                f"The customer may retry the transaction with another payment method."
            )

        if inv.is_refunded:
            return (
                f"Hello! Regarding Order #{order_id} (Payment ID: {payment_id}):\n\n"
                f"This payment of INR {gross:,.2f} was successfully refunded back to the customer's source account. "
                f"A reversal entry has been recorded in your merchant ledger."
            )

        if is_pending_within_sla:
            date_str = expected_settle_date.strftime("%A, %d %b %Y") if expected_settle_date else "the next business day"
            return (
                f"Hello! We have tracked your payment for Order #{order_id} (Payment ID: {payment_id}):\n\n"
                f"✅ Payment Status: Captured successfully on {gw.get('captured_at')}\n"
                f"💰 Gross Amount: INR {gross:,.2f}\n"
                f"🏷️ Deductions (MDR + GST 18%): INR {mdr + gst:,.2f} (Base Fee: INR {mdr:,.2f}, GST: INR {gst:,.2f})\n"
                f"💵 Net Payout Scheduled: INR {net:,.2f}\n\n"
                f"⏳ Payout Status: In-flight under normal banking settlement window (T+2 business days).\n"
                f"🏦 Expected Bank Credit Date: {date_str} (excluding weekends & RBI bank holidays).\n\n"
                f"Your funds are completely safe and will be credited to your registered bank account automatically."
            )

        # Fully Settled
        setl = inv.settlement_records[0] if inv.settlement_records else {}
        utr = setl.get("utr_number", "UTR_PENDING")
        settled_at = setl.get("settled_at") or setl.get("batch_date", "recent batch")

        return (
            f"Hello! Great news regarding Order #{order_id} (Payment ID: {payment_id}):\n\n"
            f"✅ Payout Status: Successfully Settled & Credited to your Bank Account\n"
            f"💰 Gross Transaction Amount: INR {gross:,.2f}\n"
            f"🏷️ Total Fees Deducted: INR {mdr + gst:,.2f} (MDR Fee: INR {mdr:,.2f}, GST 18%: INR {gst:,.2f})\n"
            f"💵 Net Amount Transferred: INR {net:,.2f}\n\n"
            f"🏦 Bank Settlement Details:\n"
            f"  • Settlement Batch: {setl.get('settlement_id')}\n"
            f"  • Bank Reference (UTR): {utr}\n"
            f"  • Date Credited: {settled_at}\n\n"
            f"You can verify this deposit in your bank statement using the UTR number above."
        )

    def _generate_escalation_json(
        self,
        inv: InvestigationResult,
        audit: AuditResult,
        confidence: int,
        is_pending_within_sla: bool
    ) -> dict[str, Any]:
        """Generate structured FinOps JSON escalation schema."""
        primary_gw = inv.gateway_records[0] if inv.gateway_records else {}
        order_id = primary_gw.get("order_id") or inv.query_value
        payment_id = primary_gw.get("payment_id") or "UNKNOWN"
        merchant_id = primary_gw.get("merchant_id") or "UNKNOWN"

        # Determine Root Cause & Recommended Action
        if "BROKEN_SETTLEMENT_FK" in inv.missing_links:
            failure_stage = "SETTLEMENT_NODAL_DISPATCH"
            root_cause = "Foreign key mismatch between gateway payment record and settlement batch table."
            action = f"Run re-batching pipeline for payment {payment_id} and link to active nodal settlement batch."
            tier = "TIER_2_FINOPS"
        elif "SETTLEMENT_BATCH_FAILED" in inv.missing_links:
            failure_stage = "BANK_NODAL_TRANSFER"
            root_cause = "Nodal bank account settlement batch transfer returned FAILED status from partner bank."
            action = f"Check partner bank API webhook for settlement batch {primary_gw.get('settlement_id')} and re-trigger payout."
            tier = "TIER_2_BANKING_OPS"
        elif "UNBATCHED_SETTLEMENT" in inv.missing_links and not is_pending_within_sla:
            failure_stage = "BATCH_AGGREGATION"
            root_cause = f"Payment captured on {primary_gw.get('captured_at')} has exceeded T+2 SLA without being assigned to a settlement batch."
            action = f"Enqueue payment {payment_id} into immediate off-cycle settlement payout."
            tier = "TIER_1_RECON_OPS"
        elif not audit.math_verified or audit.discrepancy_payout > Decimal("0.00"):
            failure_stage = "FEE_CALCULATION_ENGINE"
            root_cause = f"Math discrepancy detected: expected net INR {audit.expected_net} vs reported net INR {audit.reported_net} (variance: INR {audit.discrepancy_payout})."
            action = "Recalculate fee schedule and adjust merchant ledger balance with discrepancy correction credit."
            tier = "TIER_3_FINANCIAL_CONTROLLER"
        else:
            failure_stage = "IDENTIFIER_LINEAGE"
            root_cause = f"Lineage broken with missing links: {inv.missing_links}"
            action = "Inspect raw transaction lifecycle in gateway audit log."
            tier = "TIER_1_RECON_OPS"

        return {
            "status": "ESCALATION_REQUIRED",
            "confidence_score": confidence,
            "investigation_summary": {
                "order_id": order_id,
                "payment_id": payment_id,
                "merchant_id": merchant_id,
                "failure_stage": failure_stage,
                "root_cause": root_cause,
                "missing_links": inv.missing_links,
                "discrepancy_amount_inr": float(audit.discrepancy_payout),
            },
            "financial_audit": {
                "gross_amount": float(audit.gross_amount),
                "mdr_fee": float(audit.mdr_fee),
                "gst_18pct": float(audit.gst),
                "expected_net": float(audit.expected_net),
                "reported_net": float(audit.reported_net),
                "math_verified": audit.math_verified,
            },
            "recommended_action": action,
            "escalation_tier": tier,
        }
