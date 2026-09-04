"""
rebuild_linked_database.py
==========================
Fintech Reconciliation Engine — Database Ingestion & Builder

Ingests the real Razorpay CSV datasets:
  1. Test_data/razorpay_gateway_transactions.csv
  2. Test_data/razorpay_settlement_batches.csv
  3. Test_data/razorpay_merchant_ledgers.csv

And builds a clean, fully indexed, queryable SQLite database (razorpay_reconciliation.sqlite).
Normalizes identifier formats (e.g., zero-padded payment_id strings) to restore valid foreign key linkages.

Usage:
    python rebuild_linked_database.py [--csv-dir ../Test_data] [--db-path razorpay_reconciliation.sqlite]
"""

import argparse
import csv
import datetime
from decimal import Decimal, ROUND_HALF_EVEN
import os
import re
import sqlite3
import sys

# Force UTF-8 on Windows consoles
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except AttributeError:
        pass

def round_inr(val: Decimal) -> Decimal:
    """Round to 2 decimal places using Banker's rounding."""
    return val.quantize(Decimal("0.01"), rounding=ROUND_HALF_EVEN)

def normalize_payment_id(raw_id: str) -> str:
    """Normalize payment_id to 10-digit zero-padded format: pay_0000016977."""
    if not raw_id:
        return ""
    m = re.search(r"\d+", raw_id)
    if m:
        num = int(m.group(0))
        return f"pay_{num:010d}"
    return raw_id

def create_schema(conn: sqlite3.Connection):
    """Create the 3 core tables with proper constraints and indexes."""
    cursor = conn.cursor()

    cursor.executescript("""
    DROP TABLE IF EXISTS merchant_ledger;
    DROP TABLE IF EXISTS gateway_transactions;
    DROP TABLE IF EXISTS bank_settlements;

    CREATE TABLE bank_settlements (
        settlement_id TEXT PRIMARY KEY,
        merchant_id TEXT,
        nodal_account_id TEXT,
        cutoff_timestamp TEXT,
        batch_date TEXT NOT NULL,
        transaction_count INTEGER,
        gross_amount_inr REAL,
        total_fee_inr REAL,
        total_gst_inr REAL,
        total_refunds_inr REAL,
        total_amount_inr REAL NOT NULL,
        currency TEXT NOT NULL DEFAULT 'INR',
        settlement_cycle TEXT,
        is_holiday_rollover INTEGER,
        clearing_rail TEXT,
        utr_number TEXT,
        beneficiary_bank_name TEXT,
        status TEXT NOT NULL,          -- 'processed', 'created', 'failed', 'not_applicable'
        created_at TEXT NOT NULL,
        settled_at TEXT,
        reconciliation_status TEXT,
        failure_reason TEXT
    );

    CREATE TABLE gateway_transactions (
        payment_id TEXT PRIMARY KEY,
        order_id TEXT NOT NULL,
        merchant_id TEXT NOT NULL,
        amount_inr REAL NOT NULL,
        currency TEXT NOT NULL DEFAULT 'INR',
        status TEXT NOT NULL,          -- 'captured', 'failed', 'refunded', 'pending'
        payment_method TEXT NOT NULL,  -- 'upi', 'card', 'netbanking', 'wallet'
        method_subtype TEXT,
        upi_rrn_or_arn TEXT,
        base_fee_inr REAL NOT NULL,
        gst_tax_18pct_inr REAL NOT NULL,
        net_amount_inr REAL NOT NULL,
        settlement_id TEXT,
        created_at TEXT NOT NULL,
        captured_at TEXT,
        settlement_status TEXT,
        FOREIGN KEY (settlement_id) REFERENCES bank_settlements (settlement_id)
    );

    CREATE TABLE merchant_ledger (
        ledger_entry_id TEXT PRIMARY KEY,
        merchant_id TEXT NOT NULL,
        timestamp TEXT NOT NULL,
        lifecycle_stage TEXT,          -- 'CAPTURE', 'PAYOUT', 'SETTLEMENT_BATCH'
        event_type TEXT,               -- 'PAYMENT_CAPTURED_GROSS', 'MDR_FEE_AND_GST_DEDUCTED', 'NODAL_PAYOUT_DISPATCHED'
        payment_id TEXT,
        settlement_id TEXT,
        account_debited TEXT,
        account_credited TEXT,
        amount_inr REAL NOT NULL,
        net_amount_inr REAL NOT NULL,
        currency TEXT NOT NULL DEFAULT 'INR',
        balance_pending_settlement REAL,
        balance_available_for_payout REAL,
        balance_in_transit REAL,
        balance_settled_cumulative REAL,
        description TEXT,
        FOREIGN KEY (payment_id) REFERENCES gateway_transactions (payment_id),
        FOREIGN KEY (settlement_id) REFERENCES bank_settlements (settlement_id)
    );

    -- Optimized Indexes for Reconciliation Queries
    CREATE INDEX idx_gw_order_id ON gateway_transactions (order_id);
    CREATE INDEX idx_gw_merchant_id ON gateway_transactions (merchant_id);
    CREATE INDEX idx_gw_settlement_id ON gateway_transactions (settlement_id);
    CREATE INDEX idx_gw_status ON gateway_transactions (status);
    CREATE INDEX idx_gw_created_at ON gateway_transactions (created_at);

    CREATE INDEX idx_setl_utr ON bank_settlements (utr_number);
    CREATE INDEX idx_setl_status ON bank_settlements (status);
    CREATE INDEX idx_setl_batch_date ON bank_settlements (batch_date);

    CREATE INDEX idx_led_payment_id ON merchant_ledger (payment_id);
    CREATE INDEX idx_led_settlement_id ON merchant_ledger (settlement_id);
    CREATE INDEX idx_led_merchant_id ON merchant_ledger (merchant_id);
    CREATE INDEX idx_led_timestamp ON merchant_ledger (timestamp);
    """)
    conn.commit()

def ingest_from_csv(csv_dir: str, db_path: str):
    """Ingest real CSV files into SQLite database."""
    print(f"[*] Ingesting CSV datasets from: {csv_dir}")
    print(f"[*] Target SQLite Database: {db_path}")

    conn = sqlite3.connect(db_path)
    create_schema(conn)
    cursor = conn.cursor()

    # 1. Ingest Settlement Batches
    setl_csv = os.path.join(csv_dir, "razorpay_settlement_batches.csv")
    setl_rows = []
    settlement_ids_set = set()

    if os.path.exists(setl_csv):
        print(f"[*] Ingesting {setl_csv}...")
        with open(setl_csv, "r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for r in reader:
                s_id = r.get("settlement_id", "").strip()
                if not s_id:
                    continue
                settlement_ids_set.add(s_id)
                cutoff = r.get("cutoff_timestamp", "")
                batch_date = cutoff[:10] if cutoff else "2026-08-01"
                total_amt = float(r.get("net_settlement_amount_inr") or r.get("gross_amount_inr") or 0.0)
                status = r.get("payout_status", "processed").strip()
                
                setl_rows.append((
                    s_id,
                    r.get("merchant_id", ""),
                    r.get("nodal_account_id", ""),
                    cutoff,
                    batch_date,
                    int(r.get("transaction_count") or 0),
                    float(r.get("gross_amount_inr") or 0.0),
                    float(r.get("total_fee_inr") or 0.0),
                    float(r.get("total_gst_inr") or 0.0),
                    float(r.get("total_refunds_inr") or 0.0),
                    total_amt,
                    r.get("currency", "INR"),
                    r.get("settlement_cycle", "T+2"),
                    1 if r.get("is_holiday_rollover", "").lower() == "true" else 0,
                    r.get("clearing_rail", "NEFT"),
                    r.get("utr_number", ""),
                    r.get("beneficiary_bank_name", ""),
                    status,
                    cutoff,
                    r.get("bank_credit_timestamp", ""),
                    r.get("reconciliation_status", ""),
                    r.get("failure_reason", "")
                ))

        cursor.executemany("""
            INSERT INTO bank_settlements (
                settlement_id, merchant_id, nodal_account_id, cutoff_timestamp,
                batch_date, transaction_count, gross_amount_inr, total_fee_inr,
                total_gst_inr, total_refunds_inr, total_amount_inr, currency,
                settlement_cycle, is_holiday_rollover, clearing_rail, utr_number,
                beneficiary_bank_name, status, created_at, settled_at,
                reconciliation_status, failure_reason
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, setl_rows)
        conn.commit()
        print(f"    ✓ Ingested {len(setl_rows):,} settlement batches.")

    # 2. Ingest Gateway Transactions
    gw_csv = os.path.join(csv_dir, "razorpay_gateway_transactions.csv")
    gw_rows = []
    payment_ids_set = set()

    if os.path.exists(gw_csv):
        print(f"[*] Ingesting {gw_csv}...")
        with open(gw_csv, "r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for r in reader:
                p_id = r.get("payment_id", "").strip()
                if not p_id:
                    continue
                norm_pid = normalize_payment_id(p_id)
                payment_ids_set.add(norm_pid)
                
                gw_rows.append((
                    norm_pid,
                    r.get("order_id", "").strip(),
                    r.get("merchant_id", "").strip(),
                    float(r.get("amount_inr") or 0.0),
                    r.get("currency", "INR"),
                    r.get("status", "captured").strip().lower(),
                    r.get("method", "upi").strip(),
                    r.get("method_subtype", ""),
                    r.get("upi_rrn_or_arn", ""),
                    float(r.get("base_fee_inr") or 0.0),
                    float(r.get("gst_tax_18pct_inr") or 0.0),
                    float(r.get("net_amount_inr") or 0.0),
                    r.get("settlement_id", "").strip() or None,
                    r.get("created_at", "").replace("T", " ").replace("Z", ""),
                    r.get("captured_at", "").replace("T", " ").replace("Z", ""),
                    r.get("settlement_status", "")
                ))

        cursor.executemany("""
            INSERT INTO gateway_transactions (
                payment_id, order_id, merchant_id, amount_inr, currency,
                status, payment_method, method_subtype, upi_rrn_or_arn,
                base_fee_inr, gst_tax_18pct_inr, net_amount_inr, settlement_id,
                created_at, captured_at, settlement_status
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, gw_rows)
        conn.commit()
        print(f"    ✓ Ingested {len(gw_rows):,} gateway transactions.")

    # 3. Ingest Merchant Ledgers
    led_csv = os.path.join(csv_dir, "razorpay_merchant_ledgers.csv")
    led_rows = []

    if os.path.exists(led_csv):
        print(f"[*] Ingesting {led_csv}...")
        with open(led_csv, "r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for r in reader:
                l_id = r.get("ledger_id", "").strip()
                if not l_id:
                    continue
                p_id_raw = r.get("payment_id", "").strip()
                norm_pid = normalize_payment_id(p_id_raw) if p_id_raw else None
                s_id = r.get("settlement_id", "").strip() or None
                amt = float(r.get("amount_inr") or 0.0)

                led_rows.append((
                    l_id,
                    r.get("merchant_id", "").strip(),
                    r.get("timestamp", "").replace("T", " ").replace("Z", ""),
                    r.get("lifecycle_stage", ""),
                    r.get("event_type", ""),
                    norm_pid,
                    s_id,
                    r.get("account_debited", ""),
                    r.get("account_credited", ""),
                    amt,
                    amt, # net_amount_inr
                    r.get("currency", "INR"),
                    float(r.get("balance_pending_settlement") or 0.0),
                    float(r.get("balance_available_for_payout") or 0.0),
                    float(r.get("balance_in_transit") or 0.0),
                    float(r.get("balance_settled_cumulative") or 0.0),
                    r.get("description", "")
                ))

        cursor.executemany("""
            INSERT INTO merchant_ledger (
                ledger_entry_id, merchant_id, timestamp, lifecycle_stage,
                event_type, payment_id, settlement_id, account_debited,
                account_credited, amount_inr, net_amount_inr, currency,
                balance_pending_settlement, balance_available_for_payout,
                balance_in_transit, balance_settled_cumulative, description
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, led_rows)
        conn.commit()
        print(f"    ✓ Ingested {len(led_rows):,} merchant ledger records.")

    # Verification Statistics
    cursor.execute("SELECT count(*) FROM gateway_transactions")
    gw_count = cursor.fetchone()[0]

    cursor.execute("SELECT count(*) FROM bank_settlements")
    setl_count = cursor.fetchone()[0]

    cursor.execute("SELECT count(*) FROM merchant_ledger")
    led_count = cursor.fetchone()[0]

    cursor.execute("""
        SELECT count(*) 
        FROM gateway_transactions g 
        JOIN bank_settlements s ON g.settlement_id = s.settlement_id
    """)
    linked_setl = cursor.fetchone()[0]

    conn.close()

    print("\n" + "=" * 65)
    print("  DATABASE BUILD & CSV INGESTION COMPLETE")
    print("=" * 65)
    print(f"  • Gateway Transactions:     {gw_count:,}")
    print(f"  • Bank Settlements:         {setl_count:,}")
    print(f"  • Merchant Ledger Records:  {led_count:,}")
    print(f"  • Gateway-Settlement Joins: {linked_setl:,}")
    print(f"  • SQLite File Size:         {os.path.getsize(db_path) / (1024*1024):.2f} MB")
    print("=" * 65 + "\n")

def main():
    parser = argparse.ArgumentParser(description="Rebuild linked Razorpay SQLite reconciliation database from CSVs.")
    parser.add_argument("--csv-dir", type=str, default="../Test_data", help="Directory containing the CSV datasets")
    parser.add_argument("--db-path", type=str, default="razorpay_reconciliation.sqlite", help="Path to output SQLite file")
    args = parser.parse_args()

    # Fallback to local Test_data if relative path doesn't exist
    csv_dir = args.csv_dir
    if not os.path.exists(csv_dir):
        if os.path.exists("Test_data"):
            csv_dir = "Test_data"
        elif os.path.exists("../Test_data"):
            csv_dir = "../Test_data"

    ingest_from_csv(csv_dir, args.db_path)

if __name__ == "__main__":
    main()
