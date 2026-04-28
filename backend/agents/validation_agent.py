import os
import re
import json
import datetime
from typing import Any, Dict, List

from dotenv import load_dotenv
from llama_index.llms.google_genai import GoogleGenAI
from utils.cost_tracker import tracked_complete
from utils.agent_logger import log_agent_start, log_agent_end, log_agent_case, log_agent_error
from agents.prompts import VALIDATION_SYSTEM_PROMPT

# =========================================================
# CONFIG
# =========================================================
CONFIG_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "config")
load_dotenv(os.path.join(CONFIG_DIR, ".env"))

DATA_DIR   = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data")
OUTPUT_FILE = os.path.join(DATA_DIR, "validation_results.json")
LOG_FILE    = os.path.join(DATA_DIR, "validation_log.txt")

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

# =========================================================
# LABEL WHITELIST
# =========================================================
LABEL_VALID = {"spam", "non_actionable", "valid_issue"}

# =========================================================
# PROMPT INJECTION GUARDRAIL
# Patterns that indicate the email is trying to override or
# hijack the system prompt / LLM instructions.
# Detection runs BEFORE any LLM call — zero API cost.
# =========================================================
INJECTION_RE = re.compile(
    r"ignore (all )?(previous|prior|above) instructions"
    r"|disregard (your )?(system )?prompt"
    r"|you are now\b"
    r"|act as (a|an|if)\b"
    r"|new (role|persona|instructions)"
    r"|<\|.*?\|>"           # token delimiters e.g. <|endoftext|>
    r"|\[INST\]|\[/INST\]"  # LLaMA-style injection markers
    r"|\{\{.*?\}\}",        # template placeholder injection
    re.IGNORECASE | re.DOTALL,
)


def detect_prompt_injection(subject: str, body_text: str) -> bool:
    """Return True if the email subject or body contains injection patterns."""
    return bool(
        INJECTION_RE.search(subject) or INJECTION_RE.search(body_text)
    )


def sanitize_body(text: str) -> str:
    """Hard-cap body length and redact any injection patterns before LLM sees it."""
    text = text[:3000]   # never send unbounded input to the LLM
    text = INJECTION_RE.sub("[REDACTED]", text)
    return text

# =========================================================
# LLM
# =========================================================
llm = GoogleGenAI(
    model="gemini-2.5-flash",
    api_key=GEMINI_API_KEY,
)


# =========================================================
# LOGGER
# Appends a plain-text entry to validation_log.txt.
# Each entry records: timestamp, subject, sender, the full
# prompt sent to the LLM, and the raw + parsed response.
# =========================================================
def log_validation(
    subject: str,
    sender_email: str,
    prompt: str,
    raw_response: str,
    parsed: Dict[str, Any],
) -> None:
    ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    sep = "=" * 70
    entry = (
        f"\n{sep}\n"
        f"TIMESTAMP  : {ts}\n"
        f"SUBJECT    : {subject}\n"
        f"SENDER     : {sender_email}\n"
        f"{'-' * 70}\n"
        f"PROMPT SENT TO LLM:\n{prompt}\n"
        f"{'-' * 70}\n"
        f"RAW LLM RESPONSE:\n{raw_response}\n"
        f"{'-' * 70}\n"
        f"PARSED RESULT:\n"
        f"  label      = {parsed.get('label', '?')}\n"
        f"  confidence = {parsed.get('confidence', '?')}\n"
        f"  reason     = {parsed.get('reason', '?')}\n"
        f"{sep}\n"
    )
    try:
        os.makedirs(DATA_DIR, exist_ok=True)
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(entry)
    except Exception as exc:
        print(f"  [ValidationAgent] WARNING — could not write log: {exc}")


async def validate_single_case(case: Dict[str, Any]) -> Dict[str, Any]:
    subject      = case.get("subject", "")
    body_text    = case.get("body_text", "")
    sender_name  = case.get("sender_name", "Unknown")
    sender_email = case.get("sender_email", "")

    # ── INPUT GUARDRAIL: prompt injection detection ────────
    # Runs before ANY LLM call. If the email body or subject
    # contains instruction-override patterns, block it instantly
    # as spam — no API cost, no risk of the LLM being hijacked.
    if detect_prompt_injection(subject, body_text):
        print(
            f"\n  [GUARDRAIL] Prompt injection detected — blocking: {subject[:60]}"
        )
        log_agent_error(
            "ValidationAgent",
            f"Prompt injection attempt blocked from {sender_email} | subject: {subject[:80]}",
        )
        injection_result = {
            "label":              "spam",
            "reason":             "Prompt injection attempt detected in email content — blocked by guardrail.",
            "confidence":         "high",
            "injection_detected": True,
        }
        case_out = dict(case)
        case_out["validation_result"] = injection_result
        return case_out

    # ── INPUT GUARDRAIL: sanitize body before prompt build ─
    # Redact any residual injection patterns and hard-cap length
    # so the LLM never receives unbounded or manipulated input.
    body_text = sanitize_body(body_text)

    email_block = (
        f"Sender: {sender_name} <{sender_email}>\n"
        f"Subject: {subject}\n"
        f"Email body:\n{body_text}"
    )
    prompt = (
        VALIDATION_SYSTEM_PROMPT
        + "\n\nEMAIL TO CLASSIFY:\n"
        + email_block
        + "\n\nReturn ONLY the JSON object."
    )

    print(f"\n  [ValidationAgent] Validating: {subject[:60]}")

    llm_failed = False
    try:
        raw = await tracked_complete(llm, prompt, "ValidationAgent", gmail_id=case.get("gmail_id", ""))
    except Exception as e:
        print(f"  [ValidationAgent] LLM call FAILED: {type(e).__name__}: {e}")        
        log_agent_error("ValidationAgent", f"{type(e).__name__}: {e}")        
        raw = '{"label": "valid_issue", "reason": "LLM validation failed — defaulting to valid_issue", "confidence": "low"}'
        llm_failed = True

    # Strip markdown fences if present
    cleaned = re.sub(r"^```(?:json)?\s*", "", raw.strip())
    cleaned = re.sub(r"\s*```$", "", cleaned)

    try:
        result = json.loads(cleaned)
    except (json.JSONDecodeError, ValueError):
        result = {}

    # Normalise label
    label = result.get("label", "valid_issue").strip().lower()
    if label not in LABEL_VALID:
        label = "valid_issue"

    reason     = result.get("reason", "").strip()
    confidence = result.get("confidence", "medium").strip().lower()
    if confidence not in {"high", "medium", "low"}:
        confidence = "medium"

    validation_result = {
        "label":      label,
        "reason":     reason,
        "confidence": confidence,
    }

    print(
        f"  [ValidationAgent] Result: label={label}  "
        f"confidence={confidence}  reason={reason[:80]}"
    )

    # Write full prompt + raw response to validation_log.txt
    log_validation(
        subject      = subject,
        sender_email = sender_email,
        prompt       = prompt,
        raw_response = raw if not llm_failed else "(LLM call failed)",
        parsed       = validation_result,
    )

    # Write summary line to shared agent_run_log.txt
    log_agent_case(
        agent   = "ValidationAgent",
        inputs  = {
            "subject":      subject,
            "sender_email": sender_email,
            "body_length":  len(body_text),
        },
        outputs = {
            "label":      label,
            "confidence": confidence,
            "reason":     reason,
        },
    )

    case_out = dict(case)
    case_out["body_text"] = body_text          # propagate sanitized body to all downstream agents
    case_out["validation_result"] = validation_result
    return case_out


async def run_validation_agent(
    intake_cases: List[Dict[str, Any]],
) -> tuple[List[Dict[str, Any]], List[Dict[str, Any]], List[Dict[str, Any]]]:
    print("\n" + "=" * 60)
    print("  VALIDATION AGENT — Spam & relevance check")
    print(f"  Cases to validate: {len(intake_cases)}")
    print("=" * 60)

    log_agent_start("ValidationAgent", {
        "cases_in": len(intake_cases),
        "log_file": LOG_FILE,
    })

    spam_cases: List[Dict[str, Any]]           = []
    non_actionable_cases: List[Dict[str, Any]] = []
    valid_cases: List[Dict[str, Any]]          = []

    for case in intake_cases:
        validated = await validate_single_case(case)
        label = validated["validation_result"]["label"]

        if label == "spam":
            spam_cases.append(validated)
        elif label == "non_actionable":
            non_actionable_cases.append(validated)
        else:
            valid_cases.append(validated)

    print(f"\n  [ValidationAgent] Summary:")
    print(f"    valid_issue    : {len(valid_cases)}")
    print(f"    spam           : {len(spam_cases)}")
    print(f"    non_actionable : {len(non_actionable_cases)}")
    print(f"    log file       : {LOG_FILE}")

    # Save all results for inspection
    all_cases = spam_cases + non_actionable_cases + valid_cases
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(all_cases, f, indent=2, default=str)
    print(f"\n  [ValidationAgent] Results saved -> {OUTPUT_FILE}")

    log_agent_end("ValidationAgent", {
        "valid_issue":    len(valid_cases),
        "spam":           len(spam_cases),
        "non_actionable": len(non_actionable_cases),
        "saved_to":       OUTPUT_FILE,
    })

    return spam_cases, non_actionable_cases, valid_cases

