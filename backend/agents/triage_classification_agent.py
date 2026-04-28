import os
import re
import json
import asyncio
from typing import Any, Dict, List

from dotenv import load_dotenv
from llama_index.llms.google_genai import GoogleGenAI
from utils.cost_tracker import tracked_complete
from utils.agent_logger import log_agent_start, log_agent_end, log_agent_case, log_agent_error
from agents.prompts import TRIAGE_SYSTEM_PROMPT

# =========================================================
# CONFIG
# =========================================================
CONFIG_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "config")
load_dotenv(os.path.join(CONFIG_DIR, ".env"))

_DATA_DIR   = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data")
OUTPUT_FILE = os.path.join(_DATA_DIR, "triage_results.json")

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

# =========================================================
# PRIORITY NORMALISER
# Maps any free-text priority the LLM might emit → P1..P4
# =========================================================
PRIORITY_MAP = {
    "p1": "P1", "critical": "P1", "urgent": "P1",
    "p2": "P2", "high": "P2",
    "p3": "P3", "medium": "P3", "moderate": "P3",
    "p4": "P4", "low": "P4", "minor": "P4",
}

CATEGORY_VALID  = {"billing", "technical", "account", "data", "feature_request", "general"}
SENTIMENT_VALID = {"angry", "frustrated", "neutral", "positive"}
TEAM_VALID      = {"engineering", "billing", "account_management", "support", "security"}

# =========================================================
# LLM
# Note: system_prompt= is ignored by acomplete().
# The prompt is prepended directly inside _classify_single_case.
# =========================================================
llm = GoogleGenAI(
    model="gemini-2.5-flash",
    api_key=GEMINI_API_KEY,
)

# =========================================================
# ======tools=====
# TOOL: classify_single_case
# Sends one escalated case to the LLM for triage classification.
# Returns the case dict extended with triage_result.
# =========================================================
async def classify_single_case(case: Dict[str, Any]) -> Dict[str, Any]:
    subject     = case.get("subject", "")
    body_text   = case.get("body_text", "")
    sender_name = case.get("sender_name", "Customer")

    # Build context from customer_context if present
    ctx = case.get("customer_context", {})
    customer_context_block = ""
    if ctx:
        customer_context_block = (
            f"\nCUSTOMER CONTEXT:\n"
            f"  Tier          : {ctx.get('tier', 'unknown')}\n"
            f"  Open issues   : {ctx.get('open_issue_count', 0)}\n"
            f"  Past issues   : {ctx.get('total_past_issues', 0)}\n"
            f"  Account age   : {ctx.get('account_age_days', '?')} days\n"
        )

    # Confluence result — why auto-reply failed
    cr = case.get("confluence_result", {})
    confluence_block = (
        f"\nCONFLUENCE SEARCH RESULT (auto-reply failed):\n"
        f"  Docs found    : {cr.get('docs_found', 0)}\n"
        f"  Best doc      : {cr.get('doc_title', 'none')}\n"
        f"  Confidence    : {cr.get('confidence', 'low')}\n"
        f"  CQL used      : {cr.get('cql_used', '')}\n"
    )

    email_block = (
        f"Customer: {sender_name}\n"
        f"Subject: {subject}\n"
        f"Email body:\n{body_text}\n"
        f"{customer_context_block}"
        f"{confluence_block}"
    )
    prompt = (
        TRIAGE_SYSTEM_PROMPT
        + "\n\nCASE TO CLASSIFY:\n"
        + email_block
        + "\nReturn ONLY the JSON object."
    )

    print(f"\n  [Triage] Classifying: {subject[:60]}")

    try:
        raw = await tracked_complete(llm, prompt, "TriageClassificationAgent", gmail_id=case.get("gmail_id", ""))
    except Exception as e:
        print(f"  [Triage] LLM call FAILED: {type(e).__name__}: {e}")
        raw = ""

    # Strip markdown fences if present
    raw = re.sub(r"^```(?:json)?\s*", "", raw)
    raw = re.sub(r"\s*```$", "", raw)

    try:
        result = json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        result = {}

    # Normalise all fields
    raw_priority = result.get("priority", "P3")
    norm_priority = PRIORITY_MAP.get(raw_priority.strip().lower(), "P3")

    norm_category = result.get("category", "general").strip().lower()
    if norm_category not in CATEGORY_VALID:
        norm_category = "general"

    norm_sentiment = result.get("sentiment", "neutral").strip().lower()
    if norm_sentiment not in SENTIMENT_VALID:
        norm_sentiment = "neutral"

    norm_team = result.get("suggested_team", "support").strip().lower()
    if norm_team not in TEAM_VALID:
        norm_team = "support"

    triage_result = {
        "category":       norm_category,
        "priority":       norm_priority,
        "sentiment":      norm_sentiment,
        "suggested_team": norm_team,
        "summary":        result.get("summary", subject),
        "tags":           result.get("tags", []),
    }

    print(
        f"  [Triage] category={triage_result['category']}  "
        f"priority={triage_result['priority']}  "
        f"team={triage_result['suggested_team']}"
    )

    return {**case, "triage_result": triage_result}



async def run_triage_classification_agent(
    escalated_cases: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    print(f"\n[TriageClassificationAgent] Starting — {len(escalated_cases)} case(s) to classify")

    log_agent_start("TriageClassificationAgent", {
        "cases_in": len(escalated_cases),
    })

    results = await asyncio.gather(
        *[classify_single_case(case) for case in escalated_cases]
    )
    output = list(results)

    os.makedirs(_DATA_DIR, exist_ok=True)
    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2, ensure_ascii=False)

    for case in output:
        tr = case.get("triage_result", {})
        log_agent_case(
            agent   = "TriageClassificationAgent",
            inputs  = {
                "subject":      case.get("subject", ""),
                "sender_email": case.get("sender_email", ""),
            },
            outputs = {
                "category":       tr.get("category", ""),
                "priority":       tr.get("priority", ""),
                "sentiment":      tr.get("sentiment", ""),
                "suggested_team": tr.get("suggested_team", ""),
                "summary":        tr.get("summary", ""),
            },
        )

    print(f"[TriageClassificationAgent] Done \u2014 saved to {OUTPUT_FILE}")

    log_agent_end("TriageClassificationAgent", {
        "cases_classified": len(output),
        "saved_to":         OUTPUT_FILE,
    })

    return output


# =========================================================
# SAMPLE TEST
# Run directly: python agents/triage_classification_agent.py
# =========================================================
SAMPLE_CASES = [
    {
        "subject":      "Cannot access my account at all",
        "body_text":    (
            "I have been locked out of my account for 3 days. "
            "I tried the password reset but the link never arrives. "
            "I have urgent invoices to download. This is unacceptable."
        ),
        "sender_name":  "Carol",
        "sender_email": "carol@example.com",
        "customer_context": {
            "tier": "VIP",
            "open_issue_count": 2,
            "total_past_issues": 8,
            "account_age_days": 730,
        },
        "confluence_result": {
            "escalate_to_human": True,
            "reply_msg":  "",
            "confidence": "low",
            "doc_title":  "",
            "doc_url":    "",
            "cql_used":   "text ~ \"account locked\" OR text ~ \"password reset\" ORDER BY lastmodified DESC",
            "docs_found": 0,
        },
    },
    {
        "subject":      "Wrong amount charged on my last invoice",
        "body_text":    "I was billed $200 but my plan is $50/month. Please correct this.",
        "sender_name":  "Dave",
        "sender_email": "dave@example.com",
        "confluence_result": {
            "escalate_to_human": True,
            "reply_msg":  "",
            "confidence": "low",
            "doc_title":  "",
            "doc_url":    "",
            "cql_used":   "text ~ \"billing\" OR text ~ \"invoice\" ORDER BY lastmodified DESC",
            "docs_found": 0,
        },
    },
]


async def run_test():
    print("=" * 60)
    print("  TRIAGE CLASSIFICATION AGENT — SAMPLE TEST")
    print("=" * 60)
    results = await run_triage_classification_agent(SAMPLE_CASES)
    for r in results:
        tr = r.get("triage_result", {})
        print(f"\nSubject      : {r['subject']}")
        print(f"Category     : {tr.get('category')}")
        print(f"Priority     : {tr.get('priority')}")
        print(f"Sentiment    : {tr.get('sentiment')}")
        print(f"Team         : {tr.get('suggested_team')}")
        print(f"Summary      : {tr.get('summary')}")
        print(f"Tags         : {tr.get('tags')}")
