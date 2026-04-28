import os
import re
import json
import base64
from email.utils import parseaddr
from typing import Any, Dict, List, Optional, Tuple

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from utils.agent_logger import log_agent_start, log_agent_end, log_agent_case, log_agent_error

# =========================================================
# CONFIG
# =========================================================
SCOPES = ["https://www.googleapis.com/auth/gmail.modify"]
CONFIG_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "config")
CREDENTIALS_FILE = os.path.join(CONFIG_DIR, "credentials.json")
TOKEN_FILE = os.path.join(CONFIG_DIR, "token.json")

DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data")
OUTPUT_FILE = os.path.join(DATA_DIR, "intake_case.json")

# Fetch only unread inbox emails
GMAIL_QUERY = "is:unread in:inbox"
MAX_RESULTS = 10

PROCESSED_LABEL_NAME = "PROCESSED_BY_TRIAGE"
MARK_AS_READ = True
ADD_PROCESSED_LABEL = True


def authenticate() -> Credentials:
    creds = None

    if os.path.exists(TOKEN_FILE):
        creds = Credentials.from_authorized_user_file(TOKEN_FILE, SCOPES)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(CREDENTIALS_FILE, SCOPES)
            creds = flow.run_local_server(port=8000, access_type="offline", prompt="consent")

        with open(TOKEN_FILE, "w", encoding="utf-8") as token:
            token.write(creds.to_json())

    return creds

def get_header(headers: List[Dict[str, str]], name: str) -> Optional[str]:
    return next(
        (h["value"] for h in headers if h["name"].lower() == name.lower()),
        None,
    )


def parse_sender(sender_raw: Optional[str]) -> Dict[str, Optional[str]]:
    if not sender_raw:
        return {"sender_name": None, "sender_email": None}
    name, email_addr = parseaddr(sender_raw)
    return {
        "sender_name": name.strip() or None,
        "sender_email": email_addr.strip() or None,
    }


def safe_b64decode(data: str) -> Optional[str]:
    if not data:
        return None
    try:
        padding = "=" * (-len(data) % 4)
        return base64.urlsafe_b64decode(data + padding).decode("utf-8", errors="replace")
    except Exception:
        return None


def strip_html_tags(html: str) -> str:
    if not html:
        return ""
    text = re.sub(r"(?is)<(script|style).*?>.*?</\1>", " ", html)
    text = re.sub(r"(?s)<br\s*/?>", "\n", text)
    text = re.sub(r"(?s)</p>", "\n", text)
    text = re.sub(r"(?s)<.*?>", " ", text)
    text = text.replace("&nbsp;", " ")
    text = text.replace("&amp;", "&")
    text = text.replace("&lt;", "<")
    text = text.replace("&gt;", ">")
    text = re.sub(r"\r", "", text)
    text = re.sub(r"\n\s*\n\s*\n+", "\n\n", text)
    text = re.sub(r"[ \t]+", " ", text)
    return text.strip()


def extract_parts(
    payload: Dict[str, Any],
) -> Tuple[Optional[str], Optional[str], List[Dict[str, Any]]]:
    plain_text = None
    html_text = None
    attachments: List[Dict[str, Any]] = []

    def walk(part: Dict[str, Any]) -> None:
        nonlocal plain_text, html_text
        mime = part.get("mimeType", "")
        filename = part.get("filename", "")
        body = part.get("body", {})

        if filename:
            attachments.append({
                "filename": filename,
                "mimeType": mime,
                "size": body.get("size", 0),
                "attachmentId": body.get("attachmentId"),
            })

        data = body.get("data")
        if mime == "text/plain" and plain_text is None and data:
            plain_text = safe_b64decode(data)
        elif mime == "text/html" and html_text is None and data:
            html_text = safe_b64decode(data)

        for subpart in part.get("parts", []):
            walk(subpart)

    walk(payload)
    return plain_text, html_text, attachments


def extract_email_from_body(body_text: Optional[str]) -> Optional[str]:
    if not body_text:
        return None
    match = re.search(r"\b[\w\.-]+@[\w\.-]+\.\w+\b", body_text)
    return match.group(0) if match else None



def get_or_create_label(service, label_name: str) -> Optional[str]:
    results = service.users().labels().list(userId="me").execute()
    for label in results.get("labels", []):
        if label.get("name") == label_name:
            return label.get("id")

    created = service.users().labels().create(
        userId="me",
        body={
            "name": label_name,
            "labelListVisibility": "labelShow",
            "messageListVisibility": "show",
        },
    ).execute()
    return created.get("id")

def mark_message_processed(service, message_id: str, processed_label_id: Optional[str]) -> None:
    body: Dict[str, Any] = {"removeLabelIds": [], "addLabelIds": []}
    if MARK_AS_READ:
        body["removeLabelIds"].append("UNREAD")
    if ADD_PROCESSED_LABEL and processed_label_id:
        body["addLabelIds"].append(processed_label_id)
    if body["removeLabelIds"] or body["addLabelIds"]:
        service.users().messages().modify(userId="me", id=message_id, body=body).execute()


def build_email_case(msg_data: Dict[str, Any]) -> Dict[str, Any]:
    payload = msg_data.get("payload", {})
    headers = payload.get("headers", [])

    subject = get_header(headers, "Subject") or "(No Subject)"
    sender_raw = get_header(headers, "From") or "(Unknown Sender)"
    recipient = get_header(headers, "To") or "(Unknown Recipient)"
    date = get_header(headers, "Date") or "(No Date)"
    message_id = get_header(headers, "Message-ID") or msg_data.get("id")

    sender_info = parse_sender(sender_raw)
    plain_text, html_text, attachments = extract_parts(payload)

    body_text = plain_text
    body_source = "text/plain"
    if not body_text and html_text:
        body_text = strip_html_tags(html_text)
        body_source = "text/html"

    return {
        "gmail_id": msg_data.get("id"),
        "thread_id": msg_data.get("threadId"),
        "message_id": message_id,
        "subject": subject,
        "sender_raw": sender_raw,
        "sender_name": sender_info["sender_name"],
        "sender_email": sender_info["sender_email"],
        "recipient": recipient,
        "date": date,
        "snippet": msg_data.get("snippet", ""),
        "body_text": body_text,
        "body_html": html_text or "",
        "body_source": body_source if body_text else None,
        "attachments": attachments,
        "labels": msg_data.get("labelIds", []),
        # Only structural entity — no triage signals
        "entities": {
            "account_email_in_body": extract_email_from_body(body_text),
        },
    }


def fetch_unread_emails(service, max_results: int = MAX_RESULTS) -> List[Dict[str, Any]]:
    results = service.users().messages().list(
        userId="me", q=GMAIL_QUERY, maxResults=max_results
    ).execute()

    messages = results.get("messages", [])
    cases: List[Dict[str, Any]] = []
    for msg in messages:
        msg_data = service.users().messages().get(
            userId="me", id=msg["id"], format="full"
        ).execute()
        cases.append(build_email_case(msg_data))
    return cases


def save_cases(cases: List[Dict[str, Any]], filepath: str = OUTPUT_FILE) -> None:
    os.makedirs(os.path.dirname(filepath), exist_ok=True)
    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(cases, f, indent=2, ensure_ascii=False)


def run_intake_agent() -> List[Dict[str, Any]]:
    log_agent_start("IntakeAgent", {
        "gmail_query":  GMAIL_QUERY,
        "max_results":  MAX_RESULTS,
        "mark_as_read": MARK_AS_READ,
        "add_label":    ADD_PROCESSED_LABEL,
    })

    creds = authenticate()
    service = build("gmail", "v1", credentials=creds)

    print("[IntakeAgent] Connected to Gmail API")
    print(f"[IntakeAgent] Query: {GMAIL_QUERY}")

    processed_label_id = None
    if ADD_PROCESSED_LABEL:
        processed_label_id = get_or_create_label(service, PROCESSED_LABEL_NAME)

    cases = fetch_unread_emails(service, max_results=MAX_RESULTS)

    if not cases:
        print("[IntakeAgent] No unread inbox emails found.")
        log_agent_end("IntakeAgent", {"cases_found": 0, "note": "No unread emails in inbox"})
        return []

    print(f"[IntakeAgent] Found {len(cases)} unread email(s).")

    for case_obj in cases:
        mark_message_processed(
            service,
            message_id=case_obj["gmail_id"],
            processed_label_id=processed_label_id,
        )
        log_agent_case("IntakeAgent",
            inputs  = {"gmail_id": case_obj.get("gmail_id", ""), "date": case_obj.get("date", "")},
            outputs = {
                "subject":      case_obj.get("subject", ""),
                "sender_email": case_obj.get("sender_email", ""),
                "sender_name":  case_obj.get("sender_name", ""),
                "body_length":  len(case_obj.get("body_text") or ""),
                "attachments":  len(case_obj.get("attachments") or []),
            }
        )

    save_cases(cases, OUTPUT_FILE)
    print(f"[IntakeAgent] Intake cases saved to {OUTPUT_FILE}")

    log_agent_end("IntakeAgent", {
        "cases_found": len(cases),
        "saved_to":    OUTPUT_FILE,
    })
    return cases



def list_emails_preview(max_results: int = MAX_RESULTS) -> List[Dict[str, Any]]:
    creds = authenticate()
    service = build("gmail", "v1", credentials=creds)
    print("[IntakeAgent] Listing emails preview (no marking)...")
    cases = fetch_unread_emails(service, max_results=max_results)
    print(f"[IntakeAgent] Found {len(cases)} unread email(s).")
    return cases



def fetch_email_by_id(gmail_id: str) -> Dict[str, Any]:
    creds = authenticate()
    service = build("gmail", "v1", credentials=creds)
    msg_data = service.users().messages().get(
        userId="me", id=gmail_id, format="full"
    ).execute()
    case = build_email_case(msg_data)
    processed_label_id = None
    if ADD_PROCESSED_LABEL:
        processed_label_id = get_or_create_label(service, PROCESSED_LABEL_NAME)
    mark_message_processed(service, gmail_id, processed_label_id)
    return case


