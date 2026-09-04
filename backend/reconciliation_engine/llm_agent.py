"""
llm_agent.py
============
Fintech Settlement Q&A Agent — Gemini Customer Support AI Layer

Connects the deterministic Investigator + Auditor pipeline to Google Gemini API
to act as an empathetic, professional, 24/7 Fintech Customer Support Specialist for merchants.

Features:
  - Generates polished, transparent, human-like customer support responses
  - Itemizes fee splits (MDR, GST 18%), net payouts, and bank UTRs
  - Explains banking cutoff (18:00 IST) and RBI holiday settlement delays clearly
  - Reassures merchants when transactions require FinOps exception handling
  - Supports automatic model fallback (gemini-2.5-flash -> gemini-2.0-flash -> gemini-1.5-flash)
  - Zero external package dependencies (built with standard library urllib)
"""

import json
import os
import urllib.request
import urllib.error
from typing import Any, Optional

from .orchestrator import ReconciliationOrchestrator


def _load_env_file():
    """Load key-value pairs from .env file if it exists."""
    search_paths = [
        ".env",
        "../.env",
        os.path.join(os.path.dirname(__file__), ".env"),
        os.path.join(os.path.dirname(__file__), "../.env"),
        os.path.join(os.path.dirname(__file__), "../../.env"),
    ]
    for path in search_paths:
        if os.path.exists(path):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if line and not line.startswith("#") and "=" in line:
                            k, v = line.split("=", 1)
                            k, v = k.strip(), v.strip().strip("\"'")
                            if k not in os.environ and v:
                                os.environ[k] = v
            except Exception:
                pass

_load_env_file()


class SettlementLLMAgent:
    """
    Combines the Reconciliation Orchestrator with Google Gemini for intelligent Customer Support Q&A.
    """

    SUPPORTED_MODELS = [
        "gemini-2.5-flash",
        "gemini-2.0-flash",
        "gemini-1.5-flash",
    ]

    def __init__(self, orchestrator: Optional[ReconciliationOrchestrator] = None, api_key: Optional[str] = None):
        self.orchestrator = orchestrator or ReconciliationOrchestrator()
        self.gemini_api_key = api_key or os.environ.get("GEMINI_API_KEY", "")

    def set_api_key(self, api_key: str):
        """Dynamically update Gemini API key."""
        self.gemini_api_key = api_key.strip()
        os.environ["GEMINI_API_KEY"] = self.gemini_api_key

    def answer_query(self, query: str, client_api_key: Optional[str] = None) -> dict[str, Any]:
        """
        Process merchant query through the Orchestrator, then synthesize a Customer Support response via Gemini.
        """
        effective_key = client_api_key or self.gemini_api_key or os.environ.get("GEMINI_API_KEY", "")

        # Step 1: Deterministic Engine Data Extraction & Lineage Audit
        base_result = self.orchestrator.process_query(query)

        # If no API key is configured, return the deterministic result
        if not effective_key:
            return {
                **base_result,
                "ai_model": "Deterministic FinOps Engine (Add GEMINI_API_KEY in .env for Gemini AI Support)",
            }

        # Step 2: Customer Support LLM Synthesis with Gemini
        try:
            llm_text, model_used = self._call_gemini_customer_support(query, base_result, effective_key)
            return {
                **base_result,
                "ai_model": f"Gemini AI Support ({model_used})",
                "customer_support_response": llm_text,
                "message": llm_text,
            }
        except Exception as e:
            # Graceful fallback on network or API failure
            return {
                **base_result,
                "ai_model": f"Deterministic Fallback (Gemini notice: {str(e)})",
            }

    def _call_gemini_customer_support(
        self,
        query: str,
        evidence: dict[str, Any],
        api_key: str
    ) -> tuple[str, str]:
        """
        Invoke Gemini API to generate empathetic customer support responses.
        Tries latest models with automatic fallback.
        """
        system_instruction = (
            "You are a helpful, empathetic, and professional Senior Fintech Customer Support Specialist for Razorpay & Stripe merchants. "
            "A merchant is inquiring about their payment, payout, or settlement status.\n\n"
            "GUIDELINES:\n"
            "1. Greet the merchant warmly and reference their exact Order ID / Payment ID / Settlement Batch.\n"
            "2. If the settlement is SETTLED (100% confidence):\n"
            "   - Congratulate them and confirm funds have been transferred.\n"
            "   - Clearly break down: Gross Amount, MDR Fee (2%), GST (18%), and Net Transferred Payout.\n"
            "   - Highlight the Bank Reference / UTR number and transfer date so they can locate it on their bank statement.\n"
            "3. If the settlement is IN-FLIGHT PENDING (within T+2 SLA):\n"
            "   - Reassure them that the customer's payment was successfully captured.\n"
            "   - Explain the standard banking cutoff (18:00 IST) and T+2 settlement window.\n"
            "   - Provide the exact expected bank credit date.\n"
            "   - Reassure them that their funds are 100% safe.\n"
            "4. If the settlement is DELAYED / BROKEN / EXCEPTION (<85% confidence):\n"
            "   - Express sincere empathy for the delay.\n"
            "   - Explain in simple, reassuring terms that our automated reconciliation system flagged the record for manual clearing.\n"
            "   - Confirm that their funds are 100% secure and an internal priority FinOps ticket has been dispatched for expedited resolution.\n"
            "5. If the payment was REFUNDED:\n"
            "   - Explain clearly that this payment was refunded back to the customer source account, and a reversal was posted.\n"
            "6. Keep formatting neat with friendly emojis, clean bullet points, and an empathetic closing sign-off."
        )

        prompt = f"""
Merchant Inquiry: "{query}"

Reconciliation Engine Evidence:
{json.dumps(evidence, indent=2)}

Please write the complete, empathetic Customer Support message for the merchant:
"""

        last_error = None
        for model in self.SUPPORTED_MODELS:
            url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={api_key}"
            payload = {
                "contents": [
                    {
                        "parts": [{"text": f"{system_instruction}\n\n{prompt}"}]
                    }
                ],
                "generationConfig": {
                    "temperature": 0.3,
                    "maxOutputTokens": 800
                }
            }

            req = urllib.request.Request(
                url,
                data=json.dumps(payload).encode("utf-8"),
                headers={"Content-Type": "application/json"}
            )

            try:
                with urllib.request.urlopen(req, timeout=12) as response:
                    res_data = json.loads(response.read().decode("utf-8"))
                    candidates = res_data.get("candidates", [])
                    if candidates:
                        text = candidates[0].get("content", {}).get("parts", [{}])[0].get("text", "")
                        if text:
                            return text.strip(), model
            except urllib.error.HTTPError as http_err:
                last_error = http_err
                # If model not found or rate limit, try next fallback model
                continue
            except Exception as e:
                last_error = e
                continue

        raise RuntimeError(f"Gemini API call failed: {str(last_error)}")
