"""
main.py  —  Triage Flow API
────────────────────────────
FastAPI application exposing the email triage pipeline as HTTP endpoints.

Start the server (from new_backend/):
    uvicorn main:app --reload --host 0.0.0.0 --port 8080

Endpoints:
    GET  /health                     → liveness check
    GET  /emails                     → list unread Gmail inbox (no side-effects)
    POST /emails/{gmail_id}/process  → run pipeline on one email
    POST /emails/process-selected    → run pipeline on selected email IDs
    POST /trigger                    → pull ALL unread Gmail + run full pipeline
    POST /process-email              → run pipeline on a submitted email case dict
"""
import sys
import os
import traceback
import logging

import openlit

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from typing import Any, Dict, List, Optional

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from agents.intake_agent import (
    run_intake_agent,
    list_emails_preview,
    fetch_email_by_id,
)
from workflow.workflow_agent import run_workflow


# =========================================================
# App setup
# =========================================================
app = FastAPI(
    title="Triage Flow API",
    description="AI-powered customer email triage pipeline",
    version="1.0.0",
)

# Initialise OpenLIT — collects LLM spans via OpenTelemetry.
# No external collector needed; it also exposes Prometheus metrics at :8080/metrics.
openlit.init(disable_batch=True)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# =========================================================
# Pydantic models
# =========================================================
class EmailPreview(BaseModel):
    """Lightweight email card returned by GET /emails."""
    gmail_id: str
    subject: str
    sender_name: Optional[str] = None
    sender_email: Optional[str] = None
    date: Optional[str] = None
    snippet: Optional[str] = None
    body_text: Optional[str] = None
    body_html: Optional[str] = None
    status: str = "Unprocessed"


class EmailCaseRequest(BaseModel):
    """Full intake case — mirrors intake_agent.build_email_case() output."""
    gmail_id: Optional[str] = "manual"
    thread_id: Optional[str] = "manual"
    message_id: Optional[str] = None
    subject: str
    sender_raw: Optional[str] = None
    sender_name: Optional[str] = None
    sender_email: str
    recipient: Optional[str] = None
    date: Optional[str] = None
    snippet: Optional[str] = ""
    body_text: str
    body_source: Optional[str] = "text/plain"
    attachments: Optional[List[Dict[str, Any]]] = []
    labels: Optional[List[str]] = []
    entities: Optional[Dict[str, Any]] = {}


class ProcessSelectedRequest(BaseModel):
    gmail_ids: List[str]


class StepEntry(BaseModel):
    step: int
    agent: str
    status: str
    note: Optional[str] = ""


class WorkflowResponse(BaseModel):
    status: str
    cases_processed: int
    steps_run: int
    step_log: List[StepEntry]
    llm_usage: List[dict] = []
    total_cost_usd: float = 0.0
    case_results: List[dict] = []


# =========================================================
# Helpers
# =========================================================
def _case_to_preview(case: dict) -> EmailPreview:
    return EmailPreview(
        gmail_id=case.get("gmail_id", ""),
        subject=case.get("subject", "(No Subject)"),
        sender_name=case.get("sender_name") or "",
        sender_email=case.get("sender_email") or "",
        date=case.get("date") or "",
        snippet=case.get("snippet") or "",
        body_text=case.get("body_text") or "",
        body_html=case.get("body_html") or "",
        status="Unprocessed",
    )


def _state_to_response(state) -> WorkflowResponse:
    # Strip heavy/binary fields before sending over HTTP
    _STRIP_FIELDS = {"body_html", "attachments", "raw", "ticket_history"}
    clean_cases = [
        {k: v for k, v in c.items() if k not in _STRIP_FIELDS}
        for c in (state.all_cases or [])
    ]
    return WorkflowResponse(
        status=state.status,
        cases_processed=len(state.enriched_data) if state.enriched_data else 0,
        steps_run=len(state.step_log),
        step_log=[StepEntry(**e) for e in state.step_log],
        llm_usage=state.llm_usage,
        total_cost_usd=state.total_cost_usd,
        case_results=clean_cases,
    )


# =========================================================
# Endpoints — System
# =========================================================
@app.get("/health", tags=["System"])
def health():
    """Liveness check."""
    return {"status": "ok", "service": "Triage Flow API"}


# =========================================================
# Endpoints — Email listing
# =========================================================
@app.get("/emails", response_model=List[EmailPreview], tags=["Emails"])
def list_emails():
    """
    Fetch unread Gmail inbox emails without marking them as processed.
    Returns a lightweight preview list suitable for the frontend inbox view.
    """
    try:
        cases = list_emails_preview()
        return [_case_to_preview(c) for c in cases]
    except Exception as exc:
        logger.error("GET /emails error:\n%s", traceback.format_exc())
        raise HTTPException(status_code=500, detail=str(exc))


# =========================================================
# Endpoints — Pipeline
# NOTE: /emails/process-selected must be registered BEFORE
#       /emails/{gmail_id}/process to avoid route shadowing.
# =========================================================
@app.post("/emails/process-selected", response_model=WorkflowResponse, tags=["Pipeline"])
async def process_selected_emails(request: ProcessSelectedRequest):
    """
    Fetch and process a specific list of emails (by Gmail ID) through
    the full triage pipeline.
    """
    if not request.gmail_ids:
        raise HTTPException(status_code=400, detail="No gmail_ids provided")

    cases = []
    for gid in request.gmail_ids:
        try:
            case = fetch_email_by_id(gid)
            if case:
                cases.append(case)
        except Exception as exc:
            print(f"[WARN] Could not fetch email {gid}: {exc}")

    if not cases:
        return WorkflowResponse(status="NO_EMAILS", cases_processed=0, steps_run=0, step_log=[])

    final_state = await run_workflow(cases)
    return _state_to_response(final_state)


@app.post("/emails/{gmail_id}/process", response_model=WorkflowResponse, tags=["Pipeline"])
async def process_single_email(gmail_id: str):
    """
    Fetch a single Gmail email by ID, mark it as processed, and run
    it through the full triage pipeline.
    """
    try:
        case = fetch_email_by_id(gmail_id)
    except Exception as exc:
        raise HTTPException(status_code=404, detail=f"Email not found: {exc}")

    final_state = await run_workflow([case])
    return _state_to_response(final_state)


@app.post("/trigger", response_model=WorkflowResponse, tags=["Pipeline"])
async def trigger():
    """
    Pull ALL unread Gmail messages via the IntakeAgent (marks them as read),
    then run the full triage pipeline.
    """
    intake_cases = run_intake_agent()

    if not intake_cases:
        return WorkflowResponse(
            status="NO_EMAILS",
            cases_processed=0,
            steps_run=0,
            step_log=[],
        )

    final_state = await run_workflow(intake_cases)
    return _state_to_response(final_state)


@app.post("/process-email", response_model=WorkflowResponse, tags=["Pipeline"])
async def process_email(case: EmailCaseRequest):
    """
    Accept a raw email case as JSON and run it through the full pipeline.
    Useful for webhook-based ingestion or testing.
    """
    case_dict = case.model_dump()
    if not case_dict.get("entities", {}).get("account_email_in_body"):
        case_dict.setdefault("entities", {})
        case_dict["entities"]["account_email_in_body"] = case_dict["sender_email"]

    final_state = await run_workflow([case_dict])
    return _state_to_response(final_state)

