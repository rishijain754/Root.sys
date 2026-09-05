"""
api_server.py
=============
Fintech Settlement AI Backend — High-Concurrency Dynamic REST API Server

Production-ready backend API service for integrating the Settlement Q&A AI Engine.
Supports real-time data streaming, automatic CSV hot-reloading on file modification,
and live webhook/data ingestion.

Endpoints:
  - POST /api/v1/reconcile          → Main AI reconciliation endpoint (Natural language & structured input)
  - POST /api/v1/ingest/transaction → Live real-time transaction ingestion / update
  - POST /api/v1/ingest/settlement  → Live settlement batch creation / update
  - POST /api/v1/ingest/ledger      → Live merchant ledger event ingestion
  - POST /api/v1/sync/csv           → Force re-sync all CSV datasets from disk
  - GET  /api/v1/health             → Health check, live transaction count, and LLM readiness
  - GET  /api/v1/order/{order_id}   → Direct lifecycle lookup by Merchant Order ID
  - GET  /api/v1/payment/{id}       → Direct lifecycle lookup by Gateway Payment ID
  - GET  /api/v1/settlement/{id}    → Direct lifecycle lookup by Settlement Batch ID
  - GET  /api/v1/exceptions         → List recent broken lineages / escalations
  - GET  /docs                      → Interactive Swagger API Documentation

Usage:
    python api_server.py [--host 127.0.0.1] [--port 5000] [--watch]
"""

import argparse
from http.server import HTTPServer, BaseHTTPRequestHandler
import json
import os
import re
import sys
import threading
import time
import urllib.parse
from typing import Any, Optional

# Force UTF-8 on Windows consoles
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except AttributeError:
        pass

base_dir = os.path.dirname(os.path.abspath(__file__))
if base_dir not in sys.path:
    sys.path.insert(0, base_dir)

from reconciliation_engine.orchestrator import ReconciliationOrchestrator
from reconciliation_engine.llm_agent import SettlementLLMAgent
from rebuild_linked_database import ingest_from_csv

START_TIME = time.time()

SWAGGER_DOCS_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <title>Fintech Settlement AI Backend API — Docs</title>
    <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&family=Fira+Code:wght@400;500&display=swap" rel="stylesheet">
    <style>
        :root {
            --bg: #0b0f19;
            --card: #151c2c;
            --border: #222f46;
            --primary: #3b82f6;
            --accent: #10b981;
            --warn: #f59e0b;
            --text: #f1f5f9;
            --muted: #94a3b8;
        }
        * { box-sizing: border-box; margin: 0; padding: 0; }
        body { font-family: 'Inter', sans-serif; background: var(--bg); color: var(--text); padding: 30px; line-height: 1.6; }
        .container { max-width: 960px; margin: 0 auto; }
        header { margin-bottom: 30px; border-bottom: 1px solid var(--border); padding-bottom: 20px; }
        .badge { display: inline-block; background: rgba(59, 130, 246, 0.15); color: #60a5fa; border: 1px solid rgba(59, 130, 246, 0.3); padding: 3px 10px; border-radius: 20px; font-size: 12px; font-weight: 600; margin-bottom: 8px; }
        h1 { font-size: 26px; font-weight: 700; margin-bottom: 6px; }
        p.desc { color: var(--muted); font-size: 14px; }
        .endpoint-card { background: var(--card); border: 1px solid var(--border); border-radius: 10px; margin-bottom: 20px; overflow: hidden; }
        .endpoint-header { padding: 14px 18px; display: flex; align-items: center; gap: 14px; border-bottom: 1px solid var(--border); background: rgba(255, 255, 255, 0.02); }
        .method { padding: 4px 10px; border-radius: 6px; font-weight: 700; font-size: 12px; font-family: 'Fira Code', monospace; }
        .method.post { background: rgba(59, 130, 246, 0.2); color: #60a5fa; }
        .method.get { background: rgba(16, 185, 129, 0.2); color: #34d399; }
        .path { font-family: 'Fira Code', monospace; font-size: 14px; font-weight: 600; }
        .endpoint-body { padding: 18px; }
        .section-label { font-size: 12px; text-transform: uppercase; color: var(--muted); font-weight: 600; margin-bottom: 6px; }
        pre { background: #080c14; border: 1px solid #1a2336; border-radius: 6px; padding: 12px 14px; font-family: 'Fira Code', monospace; font-size: 13px; color: #38bdf8; overflow-x: auto; margin-bottom: 14px; }
    </style>
</head>
<body>
<div class="container">
    <header>
        <div class="badge">LIVE DATA STREAMING • REST API v1.0</div>
        <h1>Fintech Settlement AI Backend Service</h1>
        <p class="desc">Autonomous API engine for real-time reconciliation, live data ingestion, and FinOps exception escalations.</p>
    </header>

    <!-- POST /api/v1/reconcile -->
    <div class="endpoint-card">
        <div class="endpoint-header">
            <span class="method post">POST</span>
            <span class="path">/api/v1/reconcile</span>
            <span style="margin-left: auto; color: var(--muted); font-size: 13px;">AI Reconciliation</span>
        </div>
        <div class="endpoint-body">
            <div class="section-label">Request Body</div>
            <pre>{
  "query": "Where is my payout for order_d2c_947884?",
  "api_key": "AIzaSy..." // Optional
}</pre>
        </div>
    </div>

    <!-- POST /api/v1/ingest/transaction -->
    <div class="endpoint-card">
        <div class="endpoint-header">
            <span class="method post">POST</span>
            <span class="path">/api/v1/ingest/transaction</span>
            <span style="margin-left: auto; color: var(--muted); font-size: 13px;">Live Transaction Ingest / Update</span>
        </div>
        <div class="endpoint-body">
            <div class="section-label">Request Body</div>
            <pre>{
  "payment_id": "pay_live_001",
  "order_id": "order_live_001",
  "merchant_id": "acc_d2c_01",
  "amount_inr": 2500.00,
  "status": "captured",
  "method": "upi",
  "settlement_id": "setl_live_batch_01"
}</pre>
        </div>
    </div>

    <!-- GET /api/v1/health -->
    <div class="endpoint-card">
        <div class="endpoint-header">
            <span class="method get">GET</span>
            <span class="path">/api/v1/health</span>
            <span style="margin-left: auto; color: var(--muted); font-size: 13px;">Health Check & Live Stats</span>
        </div>
        <div class="endpoint-body">
            <div class="section-label">Example Response</div>
            <pre>{
  "status": "healthy",
  "database": {
    "total_transactions": 50001,
    "status": "connected"
  }
}</pre>
        </div>
    </div>
</div>
</body>
</html>
"""


class FintechAPIServer(BaseHTTPRequestHandler):
    orchestrator: Optional[ReconciliationOrchestrator] = None
    llm_agent: Optional[SettlementLLMAgent] = None
    csv_dir: str = "../Test_data"

    @classmethod
    def _ensure_init(cls):
        if cls.orchestrator is None:
            cls.orchestrator = ReconciliationOrchestrator()
        if cls.llm_agent is None:
            cls.llm_agent = SettlementLLMAgent(orchestrator=cls.orchestrator)

    def _send_cors_headers(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, PUT, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization, x-api-key, X-API-Key")

    def _send_json(self, status_code: int, data: dict[str, Any]):
        self.send_response(status_code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self._send_cors_headers()
        self.end_headers()
        self.wfile.write(json.dumps(data, indent=2).encode("utf-8"))

    def do_OPTIONS(self):
        self.send_response(204)
        self._send_cors_headers()
        self.end_headers()

    def do_GET(self):
        self._ensure_init()
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path.rstrip("/")
        if not path:
            path = "/"

        # 1. Serve the SETU frontend at "/"
        if path == "/" or path == "/index.html":
            frontend_path = os.path.join(
                os.path.dirname(os.path.abspath(__file__)),
                "..", "frontend", "index.html"
            )
            frontend_path = os.path.normpath(frontend_path)
            if os.path.exists(frontend_path):
                with open(frontend_path, "r", encoding="utf-8") as f:
                    html = f.read()
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self._send_cors_headers()
                self.end_headers()
                self.wfile.write(html.encode("utf-8"))
            else:
                self._send_json(404, {"error": f"Frontend not found at: {frontend_path}"})
            return

        # 2. Swagger / API Documentation
        if path == "/docs":
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self._send_cors_headers()
            self.end_headers()
            self.wfile.write(SWAGGER_DOCS_HTML.encode("utf-8"))
            return

        # 2. Health Check
        if path == "/api/v1/health":
            try:
                count_res = self.orchestrator.db.query("SELECT count(*) as c FROM gateway_transactions")
                txn_count = count_res[0]["c"] if count_res else 0
                setl_res = self.orchestrator.db.query("SELECT count(*) as c FROM bank_settlements")
                setl_count = setl_res[0]["c"] if setl_res else 0
                db_status = "connected"
            except Exception as e:
                txn_count = 0
                setl_count = 0
                db_status = f"error: {str(e)}"

            self._send_json(200, {
                "status": "healthy",
                "uptime_seconds": round(time.time() - START_TIME, 1),
                "database": {
                    "path": self.orchestrator.config.db_path,
                    "total_transactions": txn_count,
                    "total_settlements": setl_count,
                    "status": db_status
                },
                "llm_engine": {
                    "configured": bool(self.llm_agent.gemini_api_key),
                    "model": "Gemini 2.5 Flash" if self.llm_agent.gemini_api_key else "Deterministic FinOps Rule Engine"
                }
            })
            return

        # 3. Direct Order Lookup
        order_match = re.match(r"^/api/v1/order/([^/]+)$", path)
        if order_match:
            order_id = order_match.group(1)
            result = self.llm_agent.answer_query(f"Where is payout for order {order_id}?")
            self._send_json(200, result)
            return

        # 4. Direct Payment Lookup
        pay_match = re.match(r"^/api/v1/payment/([^/]+)$", path)
        if pay_match:
            payment_id = pay_match.group(1)
            result = self.llm_agent.answer_query(f"Check status for payment {payment_id}")
            self._send_json(200, result)
            return

        # 5. Direct Settlement Lookup
        setl_match = re.match(r"^/api/v1/settlement/([^/]+)$", path)
        if setl_match:
            setl_id = setl_match.group(1)
            result = self.llm_agent.answer_query(f"Check settlement batch {setl_id}")
            self._send_json(200, result)
            return

        # 6. List Exceptions
        if path == "/api/v1/exceptions":
            try:
                broken_rows = self.orchestrator.db.query("""
                    SELECT payment_id, order_id, merchant_id, amount_inr, status, created_at, settlement_id 
                    FROM gateway_transactions 
                    WHERE settlement_id LIKE 'setl_missing%' OR (status = 'captured' AND settlement_id IS NULL)
                    LIMIT 50
                """)
                self._send_json(200, {
                    "status": "success",
                    "total_exceptions_sample": len(broken_rows),
                    "exceptions": broken_rows
                })
            except Exception as e:
                self._send_json(500, {"error": str(e)})
            return

        # 7. Config status check: GET /api/v1/config
        if path == "/api/v1/config":
            has_key = bool(self.llm_agent.gemini_api_key)
            self._send_json(200, {
                "ai_active": has_key,
                "engine": "Gemini AI" if has_key else "Deterministic FinOps Engine",
                "message": "Gemini API key is configured." if has_key else "No API key set. Using deterministic engine."
            })
            return

        self._send_json(404, {"error": f"Endpoint not found: {self.path}"})

    def do_POST(self):
        self._ensure_init()
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path.rstrip("/")

        length = int(self.headers.get("Content-Length", 0))
        body_str = self.rfile.read(length).decode("utf-8") if length > 0 else "{}"
        try:
            data = json.loads(body_str) if body_str else {}
        except Exception:
            self._send_json(400, {"error": "Invalid JSON payload"})
            return

        # 1. Main AI Reconciliation: /api/v1/reconcile
        if path == "/api/v1/reconcile":
            query = data.get("query") or data.get("order_id") or data.get("payment_id") or ""
            if not query.strip():
                self._send_json(400, {"error": "Request body must contain 'query', 'order_id', or 'payment_id'"})
                return

            client_key = data.get("api_key") or self.headers.get("x-api-key") or self.headers.get("X-API-Key")
            result = self.llm_agent.answer_query(query, client_api_key=client_key)
            self._send_json(200, result)
            return

        # 2. Dynamic Transaction Ingestion: /api/v1/ingest/transaction
        if path == "/api/v1/ingest/transaction":
            if not data.get("payment_id") or not data.get("order_id"):
                self._send_json(400, {"error": "Missing required fields: 'payment_id' and 'order_id'"})
                return
            saved = self.orchestrator.db.upsert_gateway_transaction(data)
            self._send_json(201, {
                "status": "success",
                "message": f"Gateway transaction {saved['payment_id']} successfully ingested/updated.",
                "data": saved
            })
            return

        # 3. Dynamic Settlement Batch Ingestion: /api/v1/ingest/settlement
        if path == "/api/v1/ingest/settlement":
            if not data.get("settlement_id"):
                self._send_json(400, {"error": "Missing required field: 'settlement_id'"})
                return
            saved = self.orchestrator.db.upsert_bank_settlement(data)
            self._send_json(201, {
                "status": "success",
                "message": f"Settlement batch {saved['settlement_id']} successfully ingested/updated.",
                "data": saved
            })
            return

        # 4. Dynamic Ledger Entry Ingestion: /api/v1/ingest/ledger
        if path == "/api/v1/ingest/ledger":
            saved = self.orchestrator.db.upsert_merchant_ledger(data)
            self._send_json(201, {
                "status": "success",
                "message": "Merchant ledger entry successfully ingested.",
                "data": saved
            })
            return

        # 5. Force Re-sync from CSV files on disk: /api/v1/sync/csv
        if path == "/api/v1/sync/csv":
            csv_path = data.get("csv_dir") or self.csv_dir
            try:
                ingest_from_csv(csv_path, self.orchestrator.config.db_path)
                self._send_json(200, {
                    "status": "success",
                    "message": "CSV datasets successfully synchronized into database."
                })
            except Exception as e:
                self._send_json(500, {"error": f"CSV sync failed: {str(e)}"})
            return

        # 6. Set/clear Gemini API key at runtime: POST /api/v1/config
        if path == "/api/v1/config":
            api_key = data.get("api_key", "").strip()
            if api_key:
                self.llm_agent.set_api_key(api_key)
                # Persist to .env so it survives restarts
                env_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
                persisted = False
                try:
                    lines = []
                    key_written = False
                    if os.path.exists(env_path):
                        with open(env_path, "r", encoding="utf-8") as f:
                            for line in f:
                                if line.strip().startswith("GEMINI_API_KEY"):
                                    lines.append(f"GEMINI_API_KEY={api_key}\n")
                                    key_written = True
                                else:
                                    lines.append(line)
                    if not key_written:
                        lines.append(f"GEMINI_API_KEY={api_key}\n")
                    with open(env_path, "w", encoding="utf-8") as f:
                        f.writelines(lines)
                    persisted = True
                except Exception:
                    pass
                self._send_json(200, {
                    "status": "success",
                    "message": "Gemini API key saved. AI responses are now active.",
                    "persisted_to_env": persisted,
                    "ai_active": True
                })
            else:
                self.llm_agent.set_api_key("")
                self._send_json(200, {
                    "status": "success",
                    "message": "Gemini API key cleared. Using deterministic engine.",
                    "ai_active": False
                })
            return

        self._send_json(404, {"error": f"Endpoint not found: {self.path}"})

    def log_message(self, format, *args):
        print(f"[{self.address_string()}] {format % args}")


def start_file_watcher(csv_dir: str, db_path: str, poll_interval: int = 3):
    """Background thread that automatically watches for CSV dataset updates and re-syncs database."""
    last_mtimes = {}

    def get_csv_mtimes():
        mtimes = {}
        if os.path.exists(csv_dir):
            for fname in os.listdir(csv_dir):
                if fname.endswith(".csv"):
                    fpath = os.path.join(csv_dir, fname)
                    mtimes[fname] = os.path.getmtime(fpath)
        return mtimes

    last_mtimes = get_csv_mtimes()

    def watcher_loop():
        nonlocal last_mtimes
        while True:
            time.sleep(poll_interval)
            try:
                current_mtimes = get_csv_mtimes()
                changed = any(current_mtimes.get(k) != last_mtimes.get(k) for k in current_mtimes)
                if changed:
                    print("\n[Auto-Sync] Detected CSV file modification on disk. Live re-indexing database...")
                    ingest_from_csv(csv_dir, db_path)
                    last_mtimes = current_mtimes
                    print("[Auto-Sync] Database updated successfully in background!\n")
            except Exception as e:
                pass

    t = threading.Thread(target=watcher_loop, daemon=True)
    t.start()


def run_api_server(
    host: str = "127.0.0.1",
    port: int = 5000,
    db_path: Optional[str] = None,
    api_key: Optional[str] = None,
    csv_dir: str = "../Test_data",
    watch_csv: bool = True
):
    orch = ReconciliationOrchestrator(db_path=db_path)
    llm = SettlementLLMAgent(orchestrator=orch, api_key=api_key)

    FintechAPIServer.orchestrator = orch
    FintechAPIServer.llm_agent = llm
    FintechAPIServer.csv_dir = csv_dir

    # Start automated background CSV watcher
    if watch_csv and os.path.exists(csv_dir):
        start_file_watcher(csv_dir, orch.config.db_path)

    server = HTTPServer((host, port), FintechAPIServer)
    print("\n" + "=" * 68)
    print("  🚀 FINTECH SETTLEMENT AI BACKEND REST API SERVER RUNNING")
    print("=" * 68)
    print(f"  • API Root / Docs:    http://{host}:{port}/docs")
    print(f"  • AI Reconcile API:   POST http://{host}:{port}/api/v1/reconcile")
    print(f"  • Live Ingest API:    POST http://{host}:{port}/api/v1/ingest/transaction")
    print(f"  • Health Check:       GET  http://{host}:{port}/api/v1/health")
    print(f"  • Database:           {orch.config.db_path}")
    print(f"  • Dynamic Auto-Sync:  {'Active (Monitoring CSV updates)' if watch_csv else 'Manual'}")
    print(f"  • CORS Support:       Enabled (*)")
    print("=" * 68)
    print("Ready to receive requests. Press Ctrl+C to stop.\n")

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping API server...")
        server.server_close()


def main():
    parser = argparse.ArgumentParser(description="Fintech Settlement AI Backend API Server")
    parser.add_argument("--host", type=str, default="127.0.0.1", help="Host IP to bind to (default: 127.0.0.1)")
    parser.add_argument("--port", type=int, default=5000, help="Port to run API on (default: 5000)")
    parser.add_argument("--db-path", type=str, default=None, help="Path to SQLite database")
    parser.add_argument("--api-key", type=str, default=None, help="Optional Gemini API key")
    parser.add_argument("--csv-dir", type=str, default="../Test_data", help="Directory containing CSV files")
    parser.add_argument("--no-watch", action="store_true", help="Disable automatic CSV file watcher")
    args = parser.parse_args()

    run_api_server(
        host=args.host,
        port=args.port,
        db_path=args.db_path,
        api_key=args.api_key,
        csv_dir=args.csv_dir,
        watch_csv=not args.no_watch
    )


if __name__ == "__main__":
    main()
