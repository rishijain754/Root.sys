"""
main.py
=======
Fintech Reconciliation Engine — CLI Entry Point

Provides an interactive REPL and batch execution modes for merchant query reconciliation.

Usage:
    # Interactive REPL:
    python -m reconciliation_engine.main
    # or
    python main.py

    # Single Query One-shot:
    python main.py --query "Where is my payout for order_d2c_947884?"

    # Batch File Mode:
    python main.py --file queries.txt
"""

import argparse
import json
import os
import sys

# Force UTF-8 on Windows consoles to prevent cp1252 encode errors
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except AttributeError:
        pass

# Support running directly as a script or as a module
current_dir = os.path.dirname(os.path.abspath(__file__))
parent_dir = os.path.dirname(current_dir)
if parent_dir not in sys.path:
    sys.path.insert(0, parent_dir)

from reconciliation_engine.orchestrator import ReconciliationOrchestrator
from reconciliation_engine.config import ReconciliationConfig


def print_banner(db_path: str, txn_count: int):
    print("\n" + "=" * 65)
    print("  🔍 FINTECH RECONCILIATION ORCHESTRATOR v1.0")
    print("=" * 65)
    print(f"  • Database: {os.path.basename(db_path)} ({txn_count:,} txns)")
    print("  • Sub-Agents: Investigator (SQL) + Auditor (AST Decimal)")
    print("  • Modes: Interactive REPL | Batch File | Single Query")
    print("=" * 65)
    print("Type your query below (e.g. 'Where is payout for order_d2c_947884?')")
    print("Type 'exit' or 'quit' to close.\n")


def display_result(result: dict):
    response_type = result.get("response_type")
    confidence = result.get("confidence_score", 0)

    print("\n" + "-" * 55)
    print(f"🎯 Reconciliation Result [Confidence: {confidence}%]")
    print("-" * 55)

    if response_type == "MERCHANT_MESSAGE":
        print(f"\n💬 Merchant Response:\n")
        print(result.get("message"))
    else:
        print(f"\n⚠️ Escalation Required (<85% Confidence):\n")
        print(json.dumps(result.get("payload"), indent=2))
    print("-" * 55 + "\n")


def run_repl(orchestrator: ReconciliationOrchestrator):
    # Get transaction count for display
    try:
        count = orchestrator.db.query("SELECT count(*) as c FROM gateway_transactions")[0]["c"]
    except Exception:
        count = 50000

    print_banner(orchestrator.config.db_path, count)

    while True:
        try:
            query = input("Merchant> ").strip()
            if not query:
                continue
            if query.lower() in ["exit", "quit", "q"]:
                print("Goodbye!")
                break

            result = orchestrator.process_query(query)
            display_result(result)

        except (KeyboardInterrupt, EOFError):
            print("\nExiting. Goodbye!")
            break


def main():
    parser = argparse.ArgumentParser(description="Fintech Reconciliation Orchestrator CLI")
    parser.add_argument("--db-path", type=str, default=None, help="Path to SQLite database")
    parser.add_argument("--query", "-q", type=str, default=None, help="Single query to process")
    parser.add_argument("--file", "-f", type=str, default=None, help="Batch queries text file")

    args = parser.parse_args()

    orchestrator = ReconciliationOrchestrator(db_path=args.db_path)

    if args.query:
        result = orchestrator.process_query(args.query)
        display_result(result)
    elif args.file:
        if not os.path.exists(args.file):
            print(f"Error: File not found: {args.file}")
            sys.exit(1)
        with open(args.file, "r", encoding="utf-8") as f:
            lines = [l.strip() for l in f if l.strip() and not l.startswith("#")]
        for idx, line in enumerate(lines, 1):
            print(f"\n[{idx}/{len(lines)}] Query: {line}")
            res = orchestrator.process_query(line)
            display_result(res)
    else:
        run_repl(orchestrator)


if __name__ == "__main__":
    main()
