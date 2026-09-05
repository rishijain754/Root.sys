"""
db.py
=====
Fintech Reconciliation Engine — Dynamic SQLite Database Layer

Features:
  - Concurrent Write-Ahead Logging (WAL) mode for non-blocking read/write operations
  - Dynamic live upsert APIs for transactions, bank settlements, and ledger events
  - Parameterized queries to prevent SQL injection
  - Schema mapping from domain terms to Razorpay database column names
"""

import os
import sqlite3
from typing import Any, Optional


class ReconciliationDB:
    """Dynamic SQLite connection manager supporting real-time data updates and WAL concurrency."""

    COLUMN_MAP = {
        "gateway_txn_id": "payment_id",
        "merchant_order_id": "order_id",
        "amount": "amount_inr",
        "mdr_fee": "base_fee_inr",
        "nodal_batch_id": "settlement_id",
        "utr_number": "utr_number",
        "net_payout": "net_amount_inr",
        "gst_deduction": "gst_tax_18pct_inr",
    }

    def __init__(self, db_path: str = "razorpay_reconciliation.sqlite"):
        self.db_path = db_path
        if not os.environ.get("VERCEL"):
            self._ensure_wal_mode()

    def _ensure_wal_mode(self):
        """Enable WAL mode, busy timeout, and run checkpoint to recover from any prior crash."""
        if os.path.exists(self.db_path):
            with sqlite3.connect(self.db_path) as conn:
                conn.execute("PRAGMA journal_mode=WAL;")
                conn.execute("PRAGMA busy_timeout=5000;")
                conn.execute("PRAGMA synchronous=NORMAL;")
                # Flush any orphaned WAL frames left by a previous unclean shutdown
                try:
                    conn.execute("PRAGMA wal_checkpoint(PASSIVE);")
                except Exception:
                    pass

    def _get_connection(self, read_only: bool = True) -> sqlite3.Connection:
        """Create connection with row factory and WAL configuration."""
        if read_only and os.path.exists(self.db_path):
            conn = sqlite3.connect(f"file:{self.db_path}?mode=ro", uri=True)
        else:
            conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA busy_timeout=5000;")
        return conn

    def query(self, sql: str, params: tuple = ()) -> list[dict[str, Any]]:
        """Execute a parameterized read query and return rows as dictionaries."""
        with self._get_connection(read_only=True) as conn:
            cursor = conn.cursor()
            cursor.execute(sql, params)
            rows = cursor.fetchall()
            return [dict(row) for row in rows]

    def execute(self, sql: str, params: tuple = ()) -> int:
        """Execute an insert, update, or delete operation."""
        with self._get_connection(read_only=False) as conn:
            cursor = conn.cursor()
            cursor.execute(sql, params)
            conn.commit()
            return cursor.rowcount

    def upsert_gateway_transaction(self, data: dict[str, Any]) -> dict[str, Any]:
        """
        Dynamically insert or update a gateway transaction in real-time.
        """
        sql = """
            INSERT INTO gateway_transactions (
                payment_id, order_id, merchant_id, amount_inr, currency,
                status, payment_method, method_subtype, upi_rrn_or_arn,
                base_fee_inr, gst_tax_18pct_inr, net_amount_inr, settlement_id,
                created_at, captured_at, settlement_status
            ) VALUES (
                :payment_id, :order_id, :merchant_id, :amount_inr, :currency,
                :status, :payment_method, :method_subtype, :upi_rrn_or_arn,
                :base_fee_inr, :gst_tax_18pct_inr, :net_amount_inr, :settlement_id,
                :created_at, :captured_at, :settlement_status
            )
            ON CONFLICT(payment_id) DO UPDATE SET
                order_id = excluded.order_id,
                merchant_id = excluded.merchant_id,
                amount_inr = excluded.amount_inr,
                status = excluded.status,
                payment_method = excluded.payment_method,
                base_fee_inr = excluded.base_fee_inr,
                gst_tax_18pct_inr = excluded.gst_tax_18pct_inr,
                net_amount_inr = excluded.net_amount_inr,
                settlement_id = excluded.settlement_id,
                captured_at = excluded.captured_at,
                settlement_status = excluded.settlement_status
        """
        # Set clean defaults
        payload = {
            "payment_id": data.get("payment_id", "").strip(),
            "order_id": data.get("order_id", "").strip(),
            "merchant_id": data.get("merchant_id", "acc_default").strip(),
            "amount_inr": float(data.get("amount_inr") or data.get("amount") or 0.0),
            "currency": data.get("currency", "INR"),
            "status": data.get("status", "captured").strip().lower(),
            "payment_method": data.get("payment_method") or data.get("method") or "upi",
            "method_subtype": data.get("method_subtype", ""),
            "upi_rrn_or_arn": data.get("upi_rrn_or_arn", ""),
            "base_fee_inr": float(data.get("base_fee_inr") or data.get("mdr_fee") or 0.0),
            "gst_tax_18pct_inr": float(data.get("gst_tax_18pct_inr") or data.get("gst") or 0.0),
            "net_amount_inr": float(data.get("net_amount_inr") or data.get("net_payout") or 0.0),
            "settlement_id": data.get("settlement_id") or None,
            "created_at": data.get("created_at", "").replace("T", " ").replace("Z", ""),
            "captured_at": data.get("captured_at", "").replace("T", " ").replace("Z", ""),
            "settlement_status": data.get("settlement_status", "settled")
        }

        # Auto-compute fees if missing
        if payload["base_fee_inr"] == 0.0 and payload["amount_inr"] > 0:
            payload["base_fee_inr"] = round(payload["amount_inr"] * 0.02, 2)
            payload["gst_tax_18pct_inr"] = round(payload["base_fee_inr"] * 0.18, 2)
            payload["net_amount_inr"] = round(payload["amount_inr"] - payload["base_fee_inr"] - payload["gst_tax_18pct_inr"], 2)

        with self._get_connection(read_only=False) as conn:
            conn.execute(sql, payload)
            conn.commit()

        return payload

    def upsert_bank_settlement(self, data: dict[str, Any]) -> dict[str, Any]:
        """Dynamically insert or update a bank settlement batch."""
        sql = """
            INSERT INTO bank_settlements (
                settlement_id, merchant_id, nodal_account_id, cutoff_timestamp,
                batch_date, transaction_count, gross_amount_inr, total_fee_inr,
                total_gst_inr, total_refunds_inr, total_amount_inr, currency,
                settlement_cycle, is_holiday_rollover, clearing_rail, utr_number,
                beneficiary_bank_name, status, created_at, settled_at,
                reconciliation_status, failure_reason
            ) VALUES (
                :settlement_id, :merchant_id, :nodal_account_id, :cutoff_timestamp,
                :batch_date, :transaction_count, :gross_amount_inr, :total_fee_inr,
                :total_gst_inr, :total_refunds_inr, :total_amount_inr, :currency,
                :settlement_cycle, :is_holiday_rollover, :clearing_rail, :utr_number,
                :beneficiary_bank_name, :status, :created_at, :settled_at,
                :reconciliation_status, :failure_reason
            )
            ON CONFLICT(settlement_id) DO UPDATE SET
                status = excluded.status,
                utr_number = excluded.utr_number,
                settled_at = excluded.settled_at,
                total_amount_inr = excluded.total_amount_inr,
                failure_reason = excluded.failure_reason
        """
        payload = {
            "settlement_id": data.get("settlement_id", "").strip(),
            "merchant_id": data.get("merchant_id", "").strip(),
            "nodal_account_id": data.get("nodal_account_id", ""),
            "cutoff_timestamp": data.get("cutoff_timestamp", ""),
            "batch_date": data.get("batch_date") or data.get("cutoff_timestamp", "")[:10] or "2026-08-01",
            "transaction_count": int(data.get("transaction_count") or 1),
            "gross_amount_inr": float(data.get("gross_amount_inr") or 0.0),
            "total_fee_inr": float(data.get("total_fee_inr") or 0.0),
            "total_gst_inr": float(data.get("total_gst_inr") or 0.0),
            "total_refunds_inr": float(data.get("total_refunds_inr") or 0.0),
            "total_amount_inr": float(data.get("total_amount_inr") or data.get("net_settlement_amount_inr") or 0.0),
            "currency": data.get("currency", "INR"),
            "settlement_cycle": data.get("settlement_cycle", "T+2"),
            "is_holiday_rollover": 1 if str(data.get("is_holiday_rollover")).lower() == "true" else 0,
            "clearing_rail": data.get("clearing_rail", "NEFT"),
            "utr_number": data.get("utr_number", ""),
            "beneficiary_bank_name": data.get("beneficiary_bank_name", ""),
            "status": data.get("status", "processed").strip(),
            "created_at": data.get("created_at", ""),
            "settled_at": data.get("settled_at") or data.get("bank_credit_timestamp", ""),
            "reconciliation_status": data.get("reconciliation_status", "reconciled"),
            "failure_reason": data.get("failure_reason", "")
        }

        with self._get_connection(read_only=False) as conn:
            conn.execute(sql, payload)
            conn.commit()

        return payload

    def upsert_merchant_ledger(self, data: dict[str, Any]) -> dict[str, Any]:
        """Dynamically insert a merchant ledger entry."""
        sql = """
            INSERT INTO merchant_ledger (
                ledger_entry_id, merchant_id, timestamp, lifecycle_stage,
                event_type, payment_id, settlement_id, account_debited,
                account_credited, amount_inr, net_amount_inr, currency,
                balance_pending_settlement, balance_available_for_payout,
                balance_in_transit, balance_settled_cumulative, description
            ) VALUES (
                :ledger_entry_id, :merchant_id, :timestamp, :lifecycle_stage,
                :event_type, :payment_id, :settlement_id, :account_debited,
                :account_credited, :amount_inr, :net_amount_inr, :currency,
                :balance_pending_settlement, :balance_available_for_payout,
                :balance_in_transit, :balance_settled_cumulative, :description
            )
            ON CONFLICT(ledger_entry_id) DO UPDATE SET
                amount_inr = excluded.amount_inr,
                net_amount_inr = excluded.net_amount_inr,
                description = excluded.description
        """
        amt = float(data.get("amount_inr") or data.get("amount") or 0.0)
        payload = {
            "ledger_entry_id": data.get("ledger_entry_id", f"led_{int(os.urandom(4).hex(), 16)}"),
            "merchant_id": data.get("merchant_id", "").strip(),
            "timestamp": data.get("timestamp", "").replace("T", " ").replace("Z", ""),
            "lifecycle_stage": data.get("lifecycle_stage", "CAPTURE"),
            "event_type": data.get("event_type", "PAYMENT_CAPTURED_GROSS"),
            "payment_id": data.get("payment_id") or None,
            "settlement_id": data.get("settlement_id") or None,
            "account_debited": data.get("account_debited", ""),
            "account_credited": data.get("account_credited", ""),
            "amount_inr": amt,
            "net_amount_inr": float(data.get("net_amount_inr") or amt),
            "currency": data.get("currency", "INR"),
            "balance_pending_settlement": float(data.get("balance_pending_settlement") or 0.0),
            "balance_available_for_payout": float(data.get("balance_available_for_payout") or 0.0),
            "balance_in_transit": float(data.get("balance_in_transit") or 0.0),
            "balance_settled_cumulative": float(data.get("balance_settled_cumulative") or 0.0),
            "description": data.get("description", "")
        }

        with self._get_connection(read_only=False) as conn:
            conn.execute(sql, payload)
            conn.commit()

        return payload

    def get_gateway_by_order_id(self, order_id: str) -> list[dict[str, Any]]:
        sql = "SELECT * FROM gateway_transactions WHERE order_id = ? ORDER BY created_at DESC"
        return self.query(sql, (order_id,))

    def get_gateway_by_payment_id(self, payment_id: str) -> list[dict[str, Any]]:
        sql = "SELECT * FROM gateway_transactions WHERE payment_id = ?"
        return self.query(sql, (payment_id,))

    def get_settlement_by_id(self, settlement_id: str) -> Optional[dict[str, Any]]:
        sql = "SELECT * FROM bank_settlements WHERE settlement_id = ?"
        results = self.query(sql, (settlement_id,))
        return results[0] if results else None

    def get_settlement_by_utr(self, utr_number: str) -> Optional[dict[str, Any]]:
        sql = "SELECT * FROM bank_settlements WHERE utr_number = ?"
        results = self.query(sql, (utr_number,))
        return results[0] if results else None

    def get_ledger_by_payment_id(self, payment_id: str) -> list[dict[str, Any]]:
        sql = "SELECT * FROM merchant_ledger WHERE payment_id = ? ORDER BY timestamp ASC"
        return self.query(sql, (payment_id,))

    def get_ledger_by_settlement_id(self, settlement_id: str) -> list[dict[str, Any]]:
        sql = "SELECT * FROM merchant_ledger WHERE settlement_id = ? ORDER BY timestamp ASC"
        return self.query(sql, (settlement_id,))
