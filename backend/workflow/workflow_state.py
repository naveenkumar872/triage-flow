from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


# =========================================================
# WorkflowState
# Shared state object passed through every agent in the
# pipeline.  Each agent reads what it needs and writes its
# own output section before handing state to the next step.
# =========================================================

@dataclass
class WorkflowState:
    # ── Raw intake cases from intake_agent ───────────────
    # List of parsed email case dicts produced by run_intake_agent().
    # Each case has: gmail_id, subject, sender_email, body_text,
    # attachments, entities, etc.
    intake_cases: List[Dict[str, Any]] = field(default_factory=list)

    # ── Customer context (filled by customer_context_agent) ──
    # List of enriched case dicts — each is the original intake case
    # extended with customer_profile, ticket_history, and jira_issues.
    enriched_data: Optional[List[Dict[str, Any]]] = None

    # ── Confluence search result (filled by confluence_search_agent) ──
    # List of matched doc dicts with confidence scores.
    confluence_docs: List[Dict[str, Any]] = field(default_factory=list)

    # ── Triage result (filled by triage_classification_agent) ──
    # ticket_type, urgency_score, priority (P1–P4).
    triage_result: Optional[Dict[str, Any]] = None

    # ── Routing result (filled by route_ticket_agent) ──
    # team, slack_channel, jira_project.
    routing_result: Optional[Dict[str, Any]] = None

    # ── Jira ticket (filled by jira_agent) ────────────────
    jira_ticket_id: Optional[str] = None

    # ── Reply draft (filled by auto_reply_agent or reply_draft_agent) ──
    reply_draft: Optional[str] = None

    # ── Human approval state ───────────────────────────────
    # "pending" | "approved" | "rejected"
    approval_status: str = "pending"

    # ── Workflow meta ──────────────────────────────────────
    status: str = "RUNNING"        # RUNNING | SUCCESS | ESCALATE | DUPLICATE
    current_step: int = 1
    step_log: List[Dict[str, Any]] = field(default_factory=list)

    # ── All processed cases (collected across all branches) ──
    # Accumulates every case dict regardless of which pipeline branch
    # it took (spam, duplicate, auto-reply, full escalation).
    # Each case carries the fields added by the agents that ran on it.
    all_cases: List[Dict[str, Any]] = field(default_factory=list)

    # ── LLM cost tracking (filled by CostTracker) ──────────
    # Each entry: agent, model, prompt_tokens, completion_tokens,
    # total_tokens, cost_usd, duration_ms
    llm_usage: List[Dict[str, Any]] = field(default_factory=list)
    total_cost_usd: float = 0.0


def log_step(state: WorkflowState, agent: str, status: str, note: str = "") -> None:
    """Append a step entry to state.step_log and print progress."""
    entry = {
        "step": state.current_step,
        "agent": agent,
        "status": status,
        "note": note,
    }
    state.step_log.append(entry)
    print(f"[Step {state.current_step}] {agent} → {status}" + (f" | {note}" if note else ""))
    state.current_step += 1
