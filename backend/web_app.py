"""
web_app.py
==========
PS-8: Settlement Q&A Agent Web UI & API Server

A lightweight, zero-dependency web application serving an interactive Chat UI for merchants
to ask settlement questions, inspect transaction lineages, view financial audits, and check exception escalations.

Usage:
    python web_app.py [--port 8000]
"""

import argparse
from http.server import HTTPServer, BaseHTTPRequestHandler
import json
import os
import sys
import urllib.parse

# Force UTF-8 on Windows consoles
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except AttributeError:
        pass

# Setup path
base_dir = os.path.dirname(os.path.abspath(__file__))
if base_dir not in sys.path:
    sys.path.insert(0, base_dir)

from reconciliation_engine.orchestrator import ReconciliationOrchestrator
from reconciliation_engine.llm_agent import SettlementLLMAgent

HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>PS-8: Settlement Q&A Agent — Fintech Support</title>
    <link href="https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700&display=swap" rel="stylesheet">
    <style>
        :root {
            --bg: #0f172a;
            --card-bg: #1e293b;
            --sidebar-bg: #111827;
            --primary: #3b82f6;
            --primary-hover: #2563eb;
            --text-main: #f8fafc;
            --text-muted: #94a3b8;
            --border: #334155;
            --success: #10b981;
            --warning: #f59e0b;
            --danger: #ef4444;
        }

        * { box-sizing: border-box; margin: 0; padding: 0; }
        body {
            font-family: 'Inter', sans-serif;
            background-color: var(--bg);
            color: var(--text-main);
            display: flex;
            height: 100vh;
            overflow: hidden;
        }

        /* Sidebar */
        .sidebar {
            width: 320px;
            background: var(--sidebar-bg);
            border-right: 1px solid var(--border);
            padding: 24px 20px;
            display: flex;
            flex-direction: column;
            gap: 20px;
        }
        .logo-box {
            display: flex;
            align-items: center;
            gap: 12px;
        }
        .logo-icon {
            background: linear-gradient(135deg, #3b82f6, #8b5cf6);
            width: 40px;
            height: 40px;
            border-radius: 10px;
            display: flex;
            align-items: center;
            justify-content: center;
            font-size: 20px;
            font-weight: 700;
        }
        .logo-title { font-size: 16px; font-weight: 700; color: #fff; }
        .logo-sub { font-size: 12px; color: var(--text-muted); }

        .section-title {
            font-size: 12px;
            text-transform: uppercase;
            letter-spacing: 0.05em;
            color: var(--text-muted);
            font-weight: 600;
            margin-bottom: 8px;
        }

        .sample-list {
            display: flex;
            flex-direction: column;
            gap: 8px;
            overflow-y: auto;
        }
        .sample-btn {
            background: var(--card-bg);
            border: 1px solid var(--border);
            border-radius: 8px;
            padding: 10px 12px;
            text-align: left;
            color: var(--text-main);
            font-size: 13px;
            cursor: pointer;
            transition: all 0.2s;
            display: flex;
            flex-direction: column;
            gap: 4px;
        }
        .sample-btn:hover {
            border-color: var(--primary);
            background: #273549;
        }
        .sample-tag {
            font-size: 10px;
            font-weight: 600;
            padding: 2px 6px;
            border-radius: 4px;
            display: inline-block;
            width: fit-content;
        }
        .tag-settled { background: rgba(16, 185, 129, 0.2); color: #34d399; }
        .tag-pending { background: rgba(245, 158, 11, 0.2); color: #fbbf24; }
        .tag-broken { background: rgba(239, 68, 68, 0.2); color: #f87171; }
        .tag-refund { background: rgba(139, 92, 246, 0.2); color: #a78bfa; }

        /* Main Chat Area */
        .main-content {
            flex: 1;
            display: flex;
            flex-direction: column;
            background: var(--bg);
        }

        .topbar {
            height: 65px;
            border-bottom: 1px solid var(--border);
            padding: 0 28px;
            display: flex;
            align-items: center;
            justify-content: space-between;
            background: rgba(30, 41, 59, 0.5);
            backdrop-filter: blur(8px);
        }
        .topbar-title { font-size: 16px; font-weight: 600; }
        .status-badge {
            display: flex;
            align-items: center;
            gap: 6px;
            font-size: 13px;
            color: #34d399;
            background: rgba(16, 185, 129, 0.1);
            padding: 4px 10px;
            border-radius: 20px;
            border: 1px solid rgba(16, 185, 129, 0.3);
        }
        .status-dot {
            width: 8px;
            height: 8px;
            background: #10b981;
            border-radius: 50%;
            box-shadow: 0 0 8px #10b981;
        }

        .chat-container {
            flex: 1;
            overflow-y: auto;
            padding: 24px 28px;
            display: flex;
            flex-direction: column;
            gap: 20px;
        }

        .message {
            display: flex;
            gap: 12px;
            max-width: 85%;
        }
        .message.user {
            align-self: flex-end;
            flex-direction: row-reverse;
        }
        .avatar {
            width: 36px;
            height: 36px;
            border-radius: 50%;
            display: flex;
            align-items: center;
            justify-content: center;
            font-size: 14px;
            font-weight: 600;
            flex-shrink: 0;
        }
        .avatar.agent { background: #3b82f6; color: #fff; }
        .avatar.user { background: #64748b; color: #fff; }

        .bubble {
            background: var(--card-bg);
            border: 1px solid var(--border);
            border-radius: 12px;
            padding: 16px 20px;
            font-size: 14px;
            line-height: 1.6;
            white-space: pre-wrap;
        }
        .message.user .bubble {
            background: #2563eb;
            border-color: #3b82f6;
            color: #fff;
        }

        .confidence-badge {
            display: inline-block;
            margin-bottom: 10px;
            padding: 3px 8px;
            border-radius: 6px;
            font-size: 11px;
            font-weight: 700;
            letter-spacing: 0.05em;
        }
        .conf-100 { background: rgba(16, 185, 129, 0.2); color: #34d399; border: 1px solid #10b981; }
        .conf-low { background: rgba(239, 68, 68, 0.2); color: #f87171; border: 1px solid #ef4444; }

        .json-box {
            background: #090d16;
            border: 1px solid #1e293b;
            border-radius: 8px;
            padding: 12px;
            font-family: monospace;
            font-size: 12px;
            color: #38bdf8;
            overflow-x: auto;
            margin-top: 10px;
        }

        /* Input Bar */
        .input-bar {
            padding: 16px 28px 24px;
            background: var(--sidebar-bg);
            border-top: 1px solid var(--border);
            display: flex;
            gap: 12px;
        }
        .chat-input {
            flex: 1;
            background: var(--card-bg);
            border: 1px solid var(--border);
            border-radius: 10px;
            padding: 14px 18px;
            color: var(--text-main);
            font-size: 14px;
            font-family: inherit;
            outline: none;
            transition: border-color 0.2s;
        }
        .chat-input:focus { border-color: var(--primary); }
        .send-btn {
            background: var(--primary);
            color: #fff;
            border: none;
            border-radius: 10px;
            padding: 0 24px;
            font-weight: 600;
            cursor: pointer;
            transition: background 0.2s;
        }
        .send-btn:hover { background: var(--primary-hover); }
    </style>
</head>
<body>

    <!-- Sidebar -->
    <div class="sidebar">
        <div class="logo-box">
            <div class="logo-icon">⚡</div>
            <div>
                <div class="logo-title">Settlement Q&A</div>
                <div class="logo-sub">Fintech Support Agent (PS-8)</div>
            </div>
        </div>

        <div>
            <div class="section-title">Quick Test Queries</div>
            <div class="sample-list">
                <button class="sample-btn" onclick="sendSample('Where is my payout for order_d2c_947884?')">
                    <span class="sample-tag tag-settled">100% Settled</span>
                    <span>Order #order_d2c_947884</span>
                </button>
                <button class="sample-btn" onclick="sendSample('What is the status of order_b2b_581993?')">
                    <span class="sample-tag tag-settled">Large B2B Settled</span>
                    <span>Order #order_b2b_581993</span>
                </button>
                <button class="sample-btn" onclick="sendSample('Can you check payout for order_pending_2?')">
                    <span class="sample-tag tag-pending">Pending (T+2 SLA)</span>
                    <span>Order #order_pending_2</span>
                </button>
                <button class="sample-btn" onclick="sendSample('Where is settlement for order_broken_3?')">
                    <span class="sample-tag tag-broken">Broken FK Escalation</span>
                    <span>Order #order_broken_3</span>
                </button>
                <button class="sample-btn" onclick="sendSample('Why did payout not arrive for order_refunded_4?')">
                    <span class="sample-tag tag-refund">Refund Reversal</span>
                    <span>Order #order_refunded_4</span>
                </button>
            </div>
        </div>

        <div>
            <div class="section-title">Gemini AI Settings</div>
            <div style="background: var(--card-bg); border: 1px solid var(--border); border-radius: 8px; padding: 10px 12px; display: flex; flex-direction: column; gap: 6px;">
                <label style="font-size: 11px; color: var(--text-muted);">Gemini API Key (Optional):</label>
                <input type="password" id="apiKeyInput" placeholder="AIzaSy..." style="background: #090d16; border: 1px solid var(--border); color: #fff; padding: 6px 8px; border-radius: 4px; font-size: 12px; outline: none;">
                <button onclick="saveApiKey()" style="background: #334155; color: #fff; border: none; padding: 4px 8px; border-radius: 4px; font-size: 11px; cursor: pointer; font-weight: 600;">Save Key</button>
            </div>
        </div>

        <div style="margin-top: auto; font-size: 11px; color: var(--text-muted); border-top: 1px solid var(--border); padding-top: 12px;">
            <div>Database: <b>50,000 txns</b></div>
            <div>Engines: <b>SQL + Decimal + Gemini</b></div>
        </div>
    </div>

    <!-- Main Content -->
    <div class="main-content">
        <div class="topbar">
            <div class="topbar-title">Merchant Settlement Help Desk</div>
            <div class="status-badge">
                <div class="status-dot"></div>
                <span id="engineBadge">AI Engine Active</span>
            </div>
        </div>

        <div class="chat-container" id="chat">
            <div class="message">
                <div class="avatar agent">AI</div>
                <div class="bubble">
Hello! I am your <b>Settlement Support AI Specialist</b>. 
You can ask me about any payment, order, or settlement status (e.g. <i>"Where is my payout for order_d2c_947884?"</i>).

I automatically trace transactions across Gateway logs, Bank settlements, and Merchant ledger events.
                </div>
            </div>
        </div>

        <div class="input-bar">
            <input type="text" class="chat-input" id="userInput" placeholder="Ask about an order ID (e.g. order_d2c_947884) or payment ID..." onkeypress="handleKey(event)">
            <button class="send-btn" onclick="sendMessage()">Send</button>
        </div>
    </div>

    <script>
        const chat = document.getElementById('chat');
        const input = document.getElementById('userInput');
        const apiKeyInput = document.getElementById('apiKeyInput');

        // Load saved API key on startup
        if (localStorage.getItem('gemini_api_key')) {
            apiKeyInput.value = localStorage.getItem('gemini_api_key');
        }

        function saveApiKey() {
            const key = apiKeyInput.value.trim();
            if (key) {
                localStorage.setItem('gemini_api_key', key);
                alert('Gemini API key saved in browser storage!');
            } else {
                localStorage.removeItem('gemini_api_key');
                alert('Gemini API key cleared.');
            }
        }

        function appendMessage(sender, text, data = null) {
            const msgDiv = document.createElement('div');
            msgDiv.className = `message ${sender}`;

            const avatar = document.createElement('div');
            avatar.className = `avatar ${sender}`;
            avatar.innerText = sender === 'user' ? 'YOU' : 'AI';

            const bubble = document.createElement('div');
            bubble.className = 'bubble';

            if (data && data.confidence_score !== undefined) {
                const badge = document.createElement('div');
                const isFull = data.confidence_score === 100;
                badge.className = `confidence-badge ${isFull ? 'conf-100' : 'conf-low'}`;
                badge.innerText = `CONFIDENCE: ${data.confidence_score}% ${isFull ? '✓ VERIFIED' : '⚠ ESCALATION'} • ${data.ai_model || 'Engine'}`;
                bubble.appendChild(badge);
            }

            const content = document.createElement('div');
            content.innerText = text;
            bubble.appendChild(content);

            if (data && data.response_type === 'FINOPS_ESCALATION_JSON') {
                const jsonBox = document.createElement('div');
                jsonBox.className = 'json-box';
                jsonBox.innerText = JSON.stringify(data.payload, null, 2);
                bubble.appendChild(jsonBox);
            }

            msgDiv.appendChild(avatar);
            msgDiv.appendChild(bubble);
            chat.appendChild(msgDiv);
            chat.scrollTop = chat.scrollHeight;
        }

        async function sendMessage() {
            const query = input.value.trim();
            if (!query) return;

            appendMessage('user', query);
            input.value = '';

            const savedKey = localStorage.getItem('gemini_api_key') || '';

            try {
                const response = await fetch('/api/query', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ query: query, api_key: savedKey })
                });
                const data = await response.json();
                const replyText = data.message || (data.payload ? "Exception detected — escalated to FinOps:" : "Query processed.");
                appendMessage('agent', replyText, data);
            } catch (err) {
                appendMessage('agent', 'Error connecting to reconciliation engine.');
            }
        }

        function sendSample(text) {
            input.value = text;
            sendMessage();
        }

        function handleKey(e) {
            if (e.key === 'Enter') sendMessage();
        }
    </script>
</body>
</html>
"""


class ReconciliationHTTPHandler(BaseHTTPRequestHandler):
    orchestrator = None
    llm_agent = None

    def do_GET(self):
        if self.path == "/" or self.path == "/index.html":
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(HTML_TEMPLATE.encode("utf-8"))
        else:
            self.send_response(404)
            self.end_headers()

    def do_POST(self):
        if self.path == "/api/query":
            length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(length).decode("utf-8")
            data = json.loads(body) if body else {}
            query = data.get("query", "")
            client_key = data.get("api_key")

            # Process via Agent with optional client key
            result = self.llm_agent.answer_query(query, client_api_key=client_key)

            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(json.dumps(result).encode("utf-8"))
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, format, *args):
        # Clean server logging
        pass


def run_server(port: int = 8000, db_path: str = None, api_key: str = None):
    # Initialize Engine
    orch = ReconciliationOrchestrator(db_path=db_path)
    llm = SettlementLLMAgent(orchestrator=orch, api_key=api_key)

    ReconciliationHTTPHandler.orchestrator = orch
    ReconciliationHTTPHandler.llm_agent = llm

    server = HTTPServer(("0.0.0.0", port), ReconciliationHTTPHandler)
    print("\n" + "=" * 65)
    print("  🚀 PS-8 SETTLEMENT SUPPORT AGENT WEB SERVER RUNNING")
    print("=" * 65)
    print(f"  • Web UI URL:    http://localhost:{port}")
    print(f"  • API Endpoint:  http://localhost:{port}/api/query")
    print(f"  • Database:      {orch.config.db_path}")
    print(f"  • LLM Status:    {'Gemini API Key Active' if llm.gemini_api_key else 'Deterministic Engine (Free / No Key Needed)'}")
    print("=" * 65)
    print("Press Ctrl+C to stop server.\n")

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping web server...")
        server.server_close()


def main():
    parser = argparse.ArgumentParser(description="PS-8 Settlement Q&A Agent Web UI Server")
    parser.add_argument("--port", type=int, default=8000, help="Port to serve web UI on (default: 8000)")
    parser.add_argument("--db-path", type=str, default=None, help="Path to SQLite database")
    parser.add_argument("--api-key", type=str, default=None, help="Optional Gemini API key")
    args = parser.parse_args()

    run_server(port=args.port, db_path=args.db_path, api_key=args.api_key)


if __name__ == "__main__":
    main()
