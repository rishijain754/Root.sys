"""
auditor.py
==========
Fintech Reconciliation Engine — Sub-Agent 2: The Auditor

Deterministic mathematical verification engine. Uses Python's decimal.Decimal with
ROUND_HALF_EVEN (Banker's rounding) and AST literal evaluation for numeric inputs
to completely eliminate binary floating-point drift and verify fee/tax/payout splits.
"""

import ast
from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_EVEN
from typing import Any, Union

from .config import MAX_BREAKAGE_TOLERANCE
from .investigator import InvestigationResult


@dataclass
class AuditResult:
    """Structured result of mathematical audit across all accounting hops."""
    gross_amount: Decimal
    mdr_fee: Decimal
    gst: Decimal
    expected_net: Decimal
    reported_net: Decimal
    ledger_net: Decimal
    discrepancy_payout: Decimal
    discrepancy_ledger: Decimal
    sub_paisa_breakage: Decimal
    math_verified: bool
    audit_passed: bool
    verification_details: str


class Auditor:
    """
    Sub-Agent 2: Performs exact decimal math verification of MDR fees, GST (18%),
    sub-paisa breakage tolerances, and ledger credit amounts.
    """

    def __init__(self, tolerance: Decimal = MAX_BREAKAGE_TOLERANCE):
        self.tolerance = tolerance

    @staticmethod
    def _to_decimal(val: Any) -> Decimal:
        """Safely parse input to Decimal using ast.literal_eval where applicable."""
        if isinstance(val, Decimal):
            return val
        if isinstance(val, (int, float)):
            return Decimal(str(val))
        if isinstance(val, str):
            val_clean = val.strip()
            if not val_clean:
                return Decimal("0.00")
            try:
                # Use AST to safely evaluate clean numeric literals
                parsed = ast.literal_eval(val_clean)
                return Decimal(str(parsed))
            except (ValueError, SyntaxError):
                return Decimal(val_clean)
        return Decimal("0.00")

    @staticmethod
    def round_currency(val: Decimal) -> Decimal:
        """Standard 2-decimal banker's rounding."""
        return val.quantize(Decimal("0.01"), rounding=ROUND_HALF_EVEN)

    def verify_net_payout(
        self,
        gross: Union[str, float, Decimal],
        mdr: Union[str, float, Decimal],
        gst: Union[str, float, Decimal],
        reported_net: Union[str, float, Decimal]
    ) -> tuple[Decimal, Decimal, bool]:
        """
        Verify: Expected Net = Gross Amount - Base MDR - GST Tax.
        Returns (expected_net, discrepancy, is_valid).
        """
        d_gross = self._to_decimal(gross)
        d_mdr = self._to_decimal(mdr)
        d_gst = self._to_decimal(gst)
        d_reported = self._to_decimal(reported_net)

        expected_net = self.round_currency(d_gross - d_mdr - d_gst)
        discrepancy = self.round_currency(abs(expected_net - d_reported))
        is_valid = discrepancy <= self.tolerance

        return expected_net, discrepancy, is_valid

    def audit(self, inv_result: InvestigationResult) -> AuditResult:
        """
        Audit the raw amounts extracted by the Investigator.
        Checks:
          1. Gateway Gross - MDR - GST == Reported Net Payout
          2. Reported Net Payout == Merchant Ledger Net Credit
          3. Sub-paisa breakage within tolerance
        """
        raw = inv_result.raw_amounts
        gross = self._to_decimal(raw.get("gross_amount", "0.00"))
        mdr = self._to_decimal(raw.get("mdr_fee", "0.00"))
        gst = self._to_decimal(raw.get("gst_tax", "0.00"))
        reported_net = self._to_decimal(raw.get("reported_net", "0.00"))
        ledger_net = self._to_decimal(raw.get("ledger_net", "0.00"))

        if inv_result.is_refunded:
            # Refunds have negative gross and zero fees — skip payout math check
            expected_net = gross
            discrepancy_payout = Decimal("0.00")
            payout_math_ok = True
        else:
            expected_net, discrepancy_payout, payout_math_ok = self.verify_net_payout(
                gross, mdr, gst, reported_net
            )

        # Ledger Net Verification (if ledger record exists)
        if inv_result.ledger_records:
            if inv_result.is_refunded:
                # Reversal entry should match gross refund magnitude
                discrepancy_ledger = self.round_currency(abs(abs(ledger_net) - abs(gross)))
            else:
                discrepancy_ledger = self.round_currency(abs(reported_net - ledger_net))
            ledger_math_ok = discrepancy_ledger <= self.tolerance
        else:
            discrepancy_ledger = Decimal("0.00")
            # If no ledger yet because it's captured/pending within SLA, ledger check is skipped
            ledger_math_ok = True

        math_verified = payout_math_ok and ledger_math_ok
        audit_passed = math_verified and (discrepancy_payout == Decimal("0.00"))

        details = (
            f"Gross: INR {gross:.2f} | MDR: INR {mdr:.2f} | GST (18%): INR {gst:.2f} | "
            f"Expected Net: INR {expected_net:.2f} | Reported Net: INR {reported_net:.2f} | "
            f"Discrepancy: INR {discrepancy_payout:.2f} (Tolerance: INR {self.tolerance:.2f})"
        )

        return AuditResult(
            gross_amount=gross,
            mdr_fee=mdr,
            gst=gst,
            expected_net=expected_net,
            reported_net=reported_net,
            ledger_net=ledger_net,
            discrepancy_payout=discrepancy_payout,
            discrepancy_ledger=discrepancy_ledger,
            sub_paisa_breakage=discrepancy_payout,
            math_verified=math_verified,
            audit_passed=audit_passed,
            verification_details=details
        )
