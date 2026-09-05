"""
investigator.py
===============
Fintech Reconciliation Engine — Sub-Agent 1: The Investigator

The data-pulling and lineage inspection agent. Translates merchant queries into SQL queries,
gathers raw records across Gateway, Settlement, and Ledger tables, evaluates relationship links,
and detects any missing or broken hops in the payment lifecycle.
"""

from dataclasses import dataclass, field
import datetime
from typing import Any, Optional

from .db import ReconciliationDB


@dataclass
class InvestigationResult:
    """Structured data payload containing all raw records and relationship graph for an investigation."""
    query_type: str                         # 'order_id', 'payment_id', 'settlement_id', 'merchant_id'
    query_value: str                        # The searched identifier
    gateway_records: list[dict[str, Any]] = field(default_factory=list)
    settlement_records: list[dict[str, Any]] = field(default_factory=list)
    ledger_records: list[dict[str, Any]] = field(default_factory=list)
    linkage_chain: dict[str, Any] = field(default_factory=dict)
    missing_links: list[str] = field(default_factory=list)
    raw_amounts: dict[str, Any] = field(default_factory=dict)
    lifecycle_stage: str = "UNKNOWN"        # 'NOT_FOUND', 'INITIATED', 'CAPTURED', 'BATCHED', 'SETTLED', 'REFUNDED'
    is_refunded: bool = False
    is_failed: bool = False


class Investigator:
    """
    Sub-Agent 1: Inquires and traces data lineage across the 3 Razorpay tables.
    Pulls raw database rows without performing float arithmetic.
    """

    def __init__(self, db: ReconciliationDB):
        self.db = db

    def investigate_by_order_id(self, order_id: str) -> InvestigationResult:
        """Trace full payment lifecycle for a merchant order ID."""
        gw_rows = self.db.get_gateway_by_order_id(order_id)
        return self._assemble_investigation("order_id", order_id, gw_rows)

    def investigate_by_payment_id(self, payment_id: str) -> InvestigationResult:
        """Trace full payment lifecycle for a gateway payment ID."""
        gw_rows = self.db.get_gateway_by_payment_id(payment_id)
        return self._assemble_investigation("payment_id", payment_id, gw_rows)

    def investigate_by_settlement_id(self, settlement_id: str) -> InvestigationResult:
        """Trace all payments and ledger entries rolled into a settlement batch."""
        setl_row = self.db.get_settlement_by_id(settlement_id)
        if not setl_row:
            return InvestigationResult(
                query_type="settlement_id",
                query_value=settlement_id,
                missing_links=["SETTLEMENT_BATCH_NOT_FOUND"],
                lifecycle_stage="NOT_FOUND"
            )

        # Get linked gateway txns and ledger entries
        gw_rows = self.db.query(
            "SELECT * FROM gateway_transactions WHERE settlement_id = ?",
            (settlement_id,)
        )
        led_rows = self.db.get_ledger_by_settlement_id(settlement_id)

        res = InvestigationResult(
            query_type="settlement_id",
            query_value=settlement_id,
            gateway_records=gw_rows,
            settlement_records=[setl_row],
            ledger_records=led_rows,
            lifecycle_stage="SETTLED" if setl_row.get("status") == "SETTLED" else "BATCHED"
        )
        return res

    def _assemble_investigation(
        self,
        query_type: str,
        query_val: str,
        gw_rows: list[dict[str, Any]]
    ) -> InvestigationResult:
        """Assemble records across Gateway, Settlement, and Ledger, and detect broken links."""
        res = InvestigationResult(query_type=query_type, query_value=query_val)

        if not gw_rows:
            res.missing_links.append("MISSING_GATEWAY_RECORD")
            res.lifecycle_stage = "NOT_FOUND"
            return res

        res.gateway_records = gw_rows
        # Choose primary (most recent or captured) transaction
        primary_gw = gw_rows[0]
        for row in gw_rows:
            if row.get("status") == "captured":
                primary_gw = row
                break

        gw_status = primary_gw.get("status", "").lower()
        payment_id = primary_gw.get("payment_id")
        settlement_id = primary_gw.get("settlement_id")

        res.linkage_chain = {
            "order_id": primary_gw.get("order_id"),
            "payment_id": payment_id,
            "merchant_id": primary_gw.get("merchant_id"),
            "settlement_id": settlement_id,
            "utr_number": None,
        }

        # Check transaction status
        if gw_status == "failed":
            res.is_failed = True
            res.lifecycle_stage = "FAILED"
            res.missing_links.append("GATEWAY_PAYMENT_FAILED")
            return res

        if gw_status == "refunded":
            res.is_refunded = True
            res.lifecycle_stage = "REFUNDED"

        # Step 2: Fetch Settlement Batch
        setl_row = None
        if settlement_id:
            setl_row = self.db.get_settlement_by_id(settlement_id)
            if setl_row:
                if primary_gw and setl_row.get("merchant_id") != primary_gw.get("merchant_id"):
                    res.missing_links.append("BROKEN_SETTLEMENT_FK")
                    res.lifecycle_stage = "BROKEN_LINK"
                else:
                    res.settlement_records.append(setl_row)
                    res.linkage_chain["utr_number"] = setl_row.get("utr_number")
                    
                    if setl_row.get("status") == "FAILED":
                        res.missing_links.append("SETTLEMENT_BATCH_FAILED")
                        res.lifecycle_stage = "FAILED_SETTLEMENT"
                    elif not setl_row.get("utr_number"):
                        res.missing_links.append("MISSING_BANK_UTR")
                        res.lifecycle_stage = "BATCHED"
                    else:
                        if not res.is_refunded:
                            res.lifecycle_stage = "SETTLED"
            else:
                # settlement_id was present in gateway table but does NOT exist in bank_settlements
                res.missing_links.append("BROKEN_SETTLEMENT_FK")
                res.lifecycle_stage = "BROKEN_LINK"
        else:
            # settlement_id is NULL
            res.missing_links.append("UNBATCHED_SETTLEMENT")
            res.lifecycle_stage = "CAPTURED"

        # Step 3: Fetch Ledger Entries
        if payment_id:
            led_rows = self.db.get_ledger_by_payment_id(payment_id)
            # Filter out collisions from flawed test data
            if primary_gw:
                gw_merchant = primary_gw.get("merchant_id")
                led_rows = [r for r in led_rows if r.get("merchant_id") == gw_merchant]
            res.ledger_records = led_rows
            if not led_rows and gw_status == "captured" and setl_row and setl_row.get("status") == "SETTLED":
                res.missing_links.append("MISSING_LEDGER_ENTRY")

        # Step 4: Extract Raw Amounts for Auditor
        res.raw_amounts = {
            "gross_amount": str(primary_gw.get("amount_inr", "0.00")),
            "mdr_fee": str(primary_gw.get("base_fee_inr", "0.00")),
            "gst_tax": str(primary_gw.get("gst_tax_18pct_inr", "0.00")),
            "reported_net": str(primary_gw.get("net_amount_inr", "0.00")),
            "bank_settlement_total": str(setl_row.get("total_amount_inr", "0.00")) if setl_row else "0.00",
            "ledger_net": str(res.ledger_records[0].get("net_amount_inr", "0.00")) if res.ledger_records else "0.00",
        }

        return res
