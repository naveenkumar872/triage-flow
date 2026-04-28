import os
import json
import base64
import asyncio
from email.mime.text import MIMEText
from typing import Any, Dict, List, Optional

from dotenv import load_dotenv
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from llama_index.llms.google_genai import GoogleGenAI
from utils.cost_tracker import tracked_complete
from utils.agent_logger import log_agent_start, log_agent_end, log_agent_case, log_agent_error
from agents.prompts import build_reply_prompt, build_auto_reply_prompt

# =========================================================
# CONFIG
# =========================================================
CONFIG_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "config")
load_dotenv(os.path.join(CONFIG_DIR, ".env"))

_DATA_DIR   = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data")
OUTPUT_FILE = os.path.join(_DATA_DIR, "reply_results.json")

CREDENTIALS_FILE = os.path.join(CONFIG_DIR, "credentials.json")
TOKEN_FILE       = os.path.join(CONFIG_DIR, "token.json")

SCOPES = ["https://www.googleapis.com/auth/gmail.send"]

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
if not GEMINI_API_KEY:
    raise RuntimeError("GEMINI_API_KEY is not set in config/.env")

llm = GoogleGenAI(model="gemini-2.5-flash", api_key=GEMINI_API_KEY)

def authenticate_gmail() -> Credentials:
    creds = None

    if os.path.exists(TOKEN_FILE):
        creds = Credentials.from_authorized_user_file(TOKEN_FILE, SCOPES)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(CREDENTIALS_FILE, SCOPES)
            creds = flow.run_local_server(port=8000)

        with open(TOKEN_FILE, "w", encoding="utf-8") as token:
            token.write(creds.to_json())

    return creds


async def draft_reply_with_llm(case: Dict[str, Any], reply_type: str = "final") -> str:
    if reply_type == "auto":
        prompt = build_auto_reply_prompt(case)
    else:
        prompt = build_reply_prompt(case)
    label      = "auto-reply" if reply_type == "auto" else "final reply"
    agent_name = "AutoReplyAgent" if reply_type == "auto" else "ReplyAgent"
    print(f"  [ReplyAgent] Drafting {label} with LLM...")
    return await tracked_complete(llm, prompt, agent_name, gmail_id=case.get("gmail_id", ""))



def build_mime_message(
    to_email: str,
    subject: str,
    body: str,
    from_email: str = "me",
) -> str:
    mime = MIMEText(body, "plain", "utf-8")
    mime["To"]      = to_email
    mime["From"]    = from_email
    mime["Subject"] = f"Re: {subject}"
    raw = base64.urlsafe_b64encode(mime.as_bytes()).decode("utf-8")
    return raw



def send_reply_email(
    service,
    to_email: str,
    subject: str,
    body: str,
) -> Dict[str, Any]:
    raw_message = build_mime_message(to_email, subject, body)
    sent = service.users().messages().send(
        userId="me",
        body={"raw": raw_message},
    ).execute()

    return {
        "status":     "sent",
        "message_id": sent.get("id", ""),
        "recipient":  to_email,
    }



async def reply_single_case(
    case: Dict[str, Any],
    service,
    reply_type: str = "final",
) -> Dict[str, Any]:
    sender_email = case.get("sender_email", "")
    subject      = case.get("subject", "No Subject")

    print(f"\n  [ReplyAgent] Processing: {subject[:60]}")
    print(f"               To: {sender_email}")

    case_out = dict(case)

    if not sender_email:
        print(f"  [ReplyAgent] No sender email — skipping")
        case_out["reply_result"] = {
            "status":  "skipped",
            "reason":  "no sender_email in case",
        }
        return case_out

    # Step 1: LLM drafts the reply
    try:
        reply_body = await draft_reply_with_llm(case, reply_type)
        print(f"  [ReplyAgent] LLM draft complete ({len(reply_body)} chars)")
    except Exception as exc:
        print(f"  [ReplyAgent] LLM draft failed: {exc}")
        case_out["reply_result"] = {
            "status": "llm_error",
            "error":  str(exc),
        }
        return case_out

    # Step 2: Send via Gmail
    try:
        result = send_reply_email(service, sender_email, subject, reply_body)
        result["draft"] = reply_body
        print(f"  [ReplyAgent] Email sent → {sender_email}  id={result['message_id']}")
        case_out["reply_result"] = result
    except Exception as exc:
        print(f"  [ReplyAgent] Gmail send failed: {exc}")
        case_out["reply_result"] = {
            "status":    "send_error",
            "recipient": sender_email,
            "draft":     reply_body,
            "error":     str(exc),
        }

    return case_out



async def run_reply_agent(
    slack_cases: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    print("\n" + "=" * 60)
    print("  REPLY AGENT — Sending customer reply emails")
    print(f"  Cases to process: {len(slack_cases)}")
    print("=" * 60)

    log_agent_start("ReplyAgent", {"cases_in": len(slack_cases)})

    print("  [ReplyAgent] Authenticating with Gmail API...")
    creds   = authenticate_gmail()
    service = build("gmail", "v1", credentials=creds)
    print("  [ReplyAgent] Gmail API ready")

    results: List[Dict[str, Any]] = []
    for case in slack_cases:
        slack_status = case.get("slack_result", {}).get("status", "")
        if slack_status != "sent":
            print(
                f"\n  [ReplyAgent] Skipping '{case.get('subject','?')[:50]}' "
                f"— slack_status='{slack_status}' (not 'sent')"
            )
            case_out = dict(case)
            case_out["reply_result"] = {
                "status": "skipped",
                "reason": f"slack_status was '{slack_status}', not 'sent'",
            }
            results.append(case_out)
            continue

        result = await reply_single_case(case, service)
        results.append(result)
        _rr = result.get("reply_result", {})
        log_agent_case(
            agent   = "ReplyAgent",
            inputs  = {"subject": case.get("subject", ""), "sender_email": case.get("sender_email", "")},
            outputs = {"status": _rr.get("status", ""), "gmail_id": _rr.get("gmail_id", "")},
        )

    # Save
    os.makedirs(_DATA_DIR, exist_ok=True)
    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2, default=str)
    print(f"\n  [ReplyAgent] Results saved -> {OUTPUT_FILE}")

    log_agent_end("ReplyAgent", {"cases_replied": len(results)})
    return results



async def run_auto_reply_agent(
    auto_reply_cases: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    print("\n" + "=" * 60)
    print("  AUTO REPLY AGENT — Sending knowledge base auto-replies")
    print(f"  Cases to process: {len(auto_reply_cases)}")
    print("=" * 60)

    log_agent_start("AutoReplyAgent", {"cases_in": len(auto_reply_cases)})

    print("  [AutoReplyAgent] Authenticating with Gmail API...")
    creds   = authenticate_gmail()
    service = build("gmail", "v1", credentials=creds)
    print("  [AutoReplyAgent] Gmail API ready")

    results: List[Dict[str, Any]] = []
    for case in auto_reply_cases:
        result = await reply_single_case(case, service, reply_type="auto")
        results.append(result)
        _ar = result.get("auto_reply_result", {})
        log_agent_case(
            agent   = "AutoReplyAgent",
            inputs  = {"subject": case.get("subject", ""), "sender_email": case.get("sender_email", "")},
            outputs = {"status": _ar.get("status", ""), "gmail_id": _ar.get("gmail_id", "")},
        )

    auto_reply_file = os.path.join(_DATA_DIR, "auto_reply_results.json")
    os.makedirs(_DATA_DIR, exist_ok=True)
    with open(auto_reply_file, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2, default=str)
    print(f"\n  [AutoReplyAgent] Results saved -> {auto_reply_file}")

    log_agent_end("AutoReplyAgent", {"cases_replied": len(results)})
    return results
  

# =========================================================
# ======tools=====
# TOOL: _build_duplicate_reply_body
# Builds a plain-text reply for duplicate cases.
# No LLM needed — purely template-based.
# Tells the customer their issue is already tracked under
# an existing Jira ticket.
# =========================================================
def build_duplicate_reply_body(case: Dict[str, Any]) -> str:
    sender_name = case.get("sender_name", "Valued Customer")
    subject     = case.get("subject", "your issue")

    dup     = case.get("duplicate_check", {})
    matched = dup.get("matched_issues", [])
    first   = matched[0] if matched else {}

    ticket_key    = first.get("key", "")
    ticket_status = first.get("status", "In Progress")

    first_name  = (
        sender_name.split()[0]
        if sender_name and sender_name not in ("Unknown", "")
        else "there"
    )
    ticket_line = (
        f"existing support ticket {ticket_key}"
        if ticket_key
        else "an existing support ticket"
    )

    return (
        f"Hi {first_name},\n\n"
        f"Thank you for reaching out regarding \"{subject}\".\n\n"
        f"After reviewing our system, we found that this issue is already being tracked "
        f"under {ticket_line} (current status: {ticket_status}). "
        f"Our team is actively working on it and you will be notified as soon as "
        f"there is an update.\n\n"
        f"If you have any additional information to share, please reply to this email "
        f"and we will update the existing case accordingly.\n\n"
        f"Thanks,\n"
        f"Customer Support Team"
    )



async def run_duplicate_reply_agent(
    duplicate_cases: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    print("\n" + "=" * 60)
    print("  DUPLICATE REPLY AGENT — Notifying duplicate senders")
    print(f"  Cases to process: {len(duplicate_cases)}")
    print("=" * 60)

    log_agent_start("DuplicateReplyAgent", {"cases_in": len(duplicate_cases)})

    print("  [DuplicateReplyAgent] Authenticating with Gmail API...")
    creds   = authenticate_gmail()
    service = build("gmail", "v1", credentials=creds)
    print("  [DuplicateReplyAgent] Gmail API ready")

    results: List[Dict[str, Any]] = []
    for case in duplicate_cases:
        sender_email = case.get("sender_email", "")
        subject      = case.get("subject", "No Subject")
        case_out     = dict(case)

        if not sender_email:
            print(f"  [DuplicateReplyAgent] No sender email — skipping")
            case_out["reply_result"] = {
                "status": "skipped",
                "reason": "no sender_email in case",
            }
            results.append(case_out)
            continue

        print(f"\n  [DuplicateReplyAgent] Processing: {subject[:60]}")
        print(f"               To: {sender_email}")

        reply_body = build_duplicate_reply_body(case)

        try:
            result = send_reply_email(service, sender_email, subject, reply_body)
            result["draft"] = reply_body
            print(
                f"  [DuplicateReplyAgent] Email sent → {sender_email}"
                f"  id={result['message_id']}"
            )
            case_out["reply_result"] = result
        except Exception as exc:
            print(f"  [DuplicateReplyAgent] Gmail send failed: {exc}")
            case_out["reply_result"] = {
                "status":    "send_error",
                "recipient": sender_email,
                "draft":     reply_body,
                "error":     str(exc),
            }

        results.append(case_out)
        _dr = case_out.get("reply_result", {})
        log_agent_case(
            agent   = "DuplicateReplyAgent",
            inputs  = {"subject": case_out.get("subject", ""), "sender_email": case_out.get("sender_email", "")},
            outputs = {"status": _dr.get("status", ""), "gmail_id": _dr.get("gmail_id", "")},
        )

    dup_reply_file = os.path.join(_DATA_DIR, "duplicate_reply_results.json")
    os.makedirs(_DATA_DIR, exist_ok=True)
    with open(dup_reply_file, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2, default=str)
    print(f"\n  [DuplicateReplyAgent] Results saved -> {dup_reply_file}")

    log_agent_end("DuplicateReplyAgent", {"cases_replied": len(results)})
    return results

