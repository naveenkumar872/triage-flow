import uuid
from typing import Any, Dict, List

from workflow.workflow_state import WorkflowState, log_step
from utils.agent_logger import log_run_start, log_run_end
from agents.validation_agent import run_validation_agent
from agents.customer_context_agent import run_customer_context_agent
from agents.confluence_search_agent import run_confluence_search_agent
from agents.triage_classification_agent import run_triage_classification_agent
from agents.jira_agent import run_jira_agent
from agents.notify_slack_agent import (
    run_notify_slack_agent,
    run_notify_slack_auto_reply,
    run_notify_slack_duplicate,
    run_notify_slack_validation,
)
from agents.reply_agent import run_reply_agent, run_auto_reply_agent, run_duplicate_reply_agent
from utils.cost_tracker import CostTracker, set_tracker


# =========================================================
# run_workflow
# ─────────────────────────────────────────────────────────
# Primary orchestrator for the full email triage pipeline.
# Called by main.py with the list of intake cases produced
# by run_intake_agent().
#
# Current state: STEP 1 only — intake cases are loaded into
# WorkflowState.  Each subsequent agent will be wired in
# here one by one as they are built.
#
# Pipeline steps:
#   Step 1  → IntakeAgent              (cases loaded into state)
#   Step 1b → ValidationAgent          (spam / non_actionable → Slack + exit)
#   Step 2  → customer_context_agent   (enrich + duplicate check)
#   Step 3  → DuplicateGate            ✅ notify Slack + send email → EXIT
#   Step 4  → confluence_search_agent  ✅ CQL search + LLM analysis
#   Step 4b → AutoReplyGate            ✅ confident answer → auto-reply → EXIT
#   Step 5  → triage_classification_agent
#   Step 6  → route_ticket_agent
#   Step 7  → jira_agent
#   Step 8  → notify_slack_agent
#   Step 9  → reply_draft_agent
#   Step 10 → human approval loop
#   Step 11 → log_resolution_agent
# =========================================================
def _flush_cost(state: WorkflowState, tracker: CostTracker) -> None:
    """Copy tracker totals into workflow state before returning."""
    state.llm_usage     = tracker.to_dict_list()
    state.total_cost_usd = tracker.total_cost_usd
    total = tracker.total_cost_usd
    print(f"\n[CostTracker] Total LLM cost this run: ${total:.6f}  ({len(tracker.entries)} call(s))")


async def run_workflow(intake_cases: List[Dict[str, Any]]) -> WorkflowState:

    # ── Initialise shared state ────────────────────────────
    state = WorkflowState(intake_cases=intake_cases)
    state.status = "RUNNING"
    run_id = str(uuid.uuid4())[:8]
    log_run_start(run_id, len(intake_cases))
    # ── Initialise LLM cost tracker for this run ──────────────────
    _cost_tracker = CostTracker()
    set_tracker(_cost_tracker)
    print("\n" + "=" * 60)
    print("  WORKFLOW STARTED")
    print(f"  Processing {len(intake_cases)} intake case(s)")
    print("=" * 60)

    # ── Step 1: Intake cases received ─────────────────────
    # Data is already in state.intake_cases from the parameter.
    # No agent call needed — just log and confirm.
    if not state.intake_cases:
        state.status = "ESCALATE"
        log_step(state, "IntakeAgent", "ESCALATE", "No intake cases to process")
        _flush_cost(state, _cost_tracker)
        return state

    log_step(
        state,
        agent="IntakeAgent",
        status="SUCCESS",
        note=f"{len(state.intake_cases)} case(s) loaded into workflow state",
    )

    # ── Step 1b: Validation gate ───────────────────────────
    # Classify every case as spam | non_actionable | valid_issue
    # before doing any expensive work (DB lookups, LLM calls, etc.).
    # Spam and non-actionable cases get a Slack notification and
    # are then dropped — they never reach the main pipeline.
    try:
        spam_cases, non_actionable_cases, valid_cases = await run_validation_agent(
            state.intake_cases
        )
        log_step(
            state,
            agent="ValidationAgent",
            status="SUCCESS",
            note=(
                f"{len(valid_cases)} valid, "
                f"{len(spam_cases)} spam, "
                f"{len(non_actionable_cases)} non-actionable"
            ),
        )
    except Exception as exc:
        log_step(state, "ValidationAgent", "FAILED", str(exc))
        # On failure, treat all cases as valid so nothing is silently dropped
        spam_cases, non_actionable_cases, valid_cases = [], [], state.intake_cases

    # ── Step 1c: Slack notify for rejected cases ──────────
    rejected_cases = spam_cases + non_actionable_cases
    if rejected_cases:
        for rc in rejected_cases:
            label   = rc.get("validation_result", {}).get("label", "?")
            subject = rc.get("subject", "(no subject)")
            sender  = rc.get("sender_email", "unknown")
            print("\n" + "-" * 60)
            print(f"  [VALIDATION REJECTED — {label.upper()}]")
            print(f"  Email   : {sender}")
            print(f"  Subject : {subject}")
            print(f"  Reason  : {rc.get('validation_result', {}).get('reason', '')}")
            print("-" * 60)
            log_step(
                state,
                agent="ValidationGate",
                status=label.upper(),
                note=f"'{subject}' from {sender} — {rc.get('validation_result', {}).get('reason', '')}",
            )
        try:
            notified = await run_notify_slack_validation(rejected_cases)
            rejected_cases = notified  # update with validation_slack_result fields
            log_step(
                state,
                agent="ValidationSlackAgent",
                status="SUCCESS",
                note=f"{len(rejected_cases)} rejected case notification(s) sent to Slack",
            )
        except Exception as exc:
            log_step(state, "ValidationSlackAgent", "FAILED", str(exc))

        # Collect rejected cases into all_cases regardless of slack outcome
        state.all_cases.extend(rejected_cases)

    # Replace intake_cases with only valid cases going forward
    state.intake_cases = valid_cases

    if not state.intake_cases:
        state.status = "SUCCESS"
        print("\n" + "=" * 60)
        print("  All cases were spam or non-actionable — pipeline complete.")
        print(f"  WORKFLOW STATUS: {state.status}")
        print("=" * 60 + "\n")
        _flush_cost(state, _cost_tracker)
        return state

    # ── Step 2: Customer context enrichment ─────────────
    try:
        enriched_cases = await run_customer_context_agent(state.intake_cases)
        state.enriched_data = enriched_cases
        log_step(
            state,
            agent="CustomerContextAgent",
            status="SUCCESS",
            note=f"{len(enriched_cases)} case(s) enriched with profile, history, and Jira issues",
        )
    except Exception as exc:
        log_step(state, "CustomerContextAgent", "FAILED", str(exc))
        state.status = "ESCALATE"
        _flush_cost(state, _cost_tracker)
        return state

    # ── Step 3: Duplicate gate ─────────────────────────────
    # For each enriched case, check if the customer_context_agent
    # flagged it as a duplicate of an existing open Jira ticket.
    # Duplicates are handled here (notify Slack + send email) and
    # then skipped — they do NOT continue down the pipeline.
    new_cases       = []
    duplicate_cases = []
    for case in state.enriched_data:
        dup = case.get("duplicate_check", {})
        if dup.get("is_duplicate"):
            subject      = case.get("subject", "(no subject)")
            sender_email = case.get("sender_email", "unknown")
            matched      = dup.get("matched_issues", [])
            first_match  = matched[0] if matched else {}

            print("\n" + "-" * 60)
            print("  [DUPLICATE DETECTED]")
            print(f"  Email   : {sender_email}")
            print(f"  Subject : {subject}")
            print(f"  Matched : {dup.get('duplicate_count', 0)} open Jira ticket(s)")
            for issue in matched:
                print(
                    f"    → {issue.get('key')}  |  {issue.get('status')}  "
                    f"|  {issue.get('priority')}  |  {issue.get('summary')}"
                )
            print()
            print(f"  [Slack]  Notifying Slack thread — linking to {first_match.get('key', 'N/A')}")
            print(f"  [Email]  Sending reply to {sender_email} — duplicate of {first_match.get('key', 'N/A')}")
            print("-" * 60)

            log_step(
                state,
                agent="DuplicateGate",
                status="DUPLICATE",
                note=(
                    f"Case '{subject}' from {sender_email} is duplicate of "
                    f"{first_match.get('key', 'N/A')} — skipped pipeline"
                ),
            )
            duplicate_cases.append(case)
        else:
            new_cases.append(case)

    # ── Step 3b: Reply email + Slack notify for duplicates ─
    if duplicate_cases:
        # First: send reply emails so reply_result is present in the Slack message
        try:
            duplicate_cases = await run_duplicate_reply_agent(duplicate_cases)
            sent_count = sum(
                1 for c in duplicate_cases
                if c.get("reply_result", {}).get("status") == "sent"
            )
            log_step(
                state,
                agent="DuplicateReplyAgent",
                status="SUCCESS",
                note=f"{sent_count}/{len(duplicate_cases)} duplicate reply email(s) sent to customers",
            )
        except Exception as exc:
            log_step(state, "DuplicateReplyAgent", "FAILED", str(exc))

        # Second: notify Slack with reply status included in the message
        try:
            dup_notified = await run_notify_slack_duplicate(duplicate_cases)
            duplicate_cases = dup_notified  # update with duplicate_slack_result fields
            log_step(
                state,
                agent="DuplicateSlackAgent",
                status="SUCCESS",
                note=f"{len(duplicate_cases)} duplicate Slack notification(s) sent",
            )
        except Exception as exc:
            log_step(state, "DuplicateSlackAgent", "FAILED", str(exc))

        # Collect duplicate cases into all_cases
        state.all_cases.extend(duplicate_cases)

    # Replace enriched_data with only non-duplicate cases going forward
    state.enriched_data = new_cases

    if not state.enriched_data:
        state.status = "SUCCESS"
        print("\n" + "=" * 60)
        print("  All cases were duplicates — pipeline complete.")
        print(f"  WORKFLOW STATUS: {state.status}")
        print("=" * 60 + "\n")
        _flush_cost(state, _cost_tracker)
        return state

    # ── Step 4: Confluence knowledge base search ──────────
    try:
        confluence_cases = await run_confluence_search_agent(state.enriched_data)
        state.enriched_data = confluence_cases
        log_step(
            state,
            agent="ConfluenceSearchAgent",
            status="SUCCESS",
            note=f"{len(confluence_cases)} case(s) processed through Confluence search",
        )
    except Exception as exc:
        log_step(state, "ConfluenceSearchAgent", "FAILED", str(exc))
        state.status = "ESCALATE"
        _flush_cost(state, _cost_tracker)
        return state

    # ── Step 4b: Auto-reply gate ───────────────────────────
    # Cases where LLM found a confident doc answer → print auto-reply.
    # Cases where escalate_to_human=True → continue to triage pipeline.
    auto_reply_cases = []
    escalate_cases   = []

    for case in state.enriched_data:
        cr = case.get("confluence_result", {})
        if not cr.get("escalate_to_human") and cr.get("reply_msg"):
            subject      = case.get("subject", "(no subject)")
            sender_email = case.get("sender_email", "unknown")
            print("\n" + "-" * 60)
            print("  [AUTO-REPLY]")
            print(f"  Email      : {sender_email}")
            print(f"  Subject    : {subject}")
            print(f"  Confidence : {cr.get('confidence')}")
            print(f"  Doc used   : {cr.get('doc_title')} — {cr.get('doc_url')}")
            print(f"  Reply      : {cr.get('reply_msg', '')[:300]}")
            print("-" * 60)
            log_step(
                state,
                agent="AutoReplyGate",
                status="AUTO_REPLIED",
                note=f"Case '{subject}' auto-replied using doc '{cr.get('doc_title')}'",
            )
            auto_reply_cases.append(case)
        else:
            escalate_cases.append(case)

    # ── Step 4c: Send auto-reply emails + notify Slack ────────
    # Auto-reply cases get a knowledge-base-grounded email sent
    # to the customer and a Slack notification to the general
    # channel. Both run before they exit the pipeline.
    if auto_reply_cases:
        # First: send the reply emails so reply_result is available
        # for the Slack message to include send status.
        try:
            auto_replied = await run_auto_reply_agent(auto_reply_cases)
            sent_count = sum(
                1 for c in auto_replied
                if c.get("reply_result", {}).get("status") == "sent"
            )
            log_step(
                state,
                agent="AutoReplyAgent",
                status="SUCCESS",
                note=f"{sent_count}/{len(auto_replied)} auto-reply email(s) sent to customers",
            )
        except Exception as exc:
            log_step(state, "AutoReplyAgent", "FAILED", str(exc))
            auto_replied = auto_reply_cases  # fall back without reply_result

        # Second: notify Slack with reply status included in the message
        try:
            ar_notified = await run_notify_slack_auto_reply(auto_replied)
            auto_replied = ar_notified  # update with auto_reply_slack_result fields
            log_step(
                state,
                agent="AutoReplySlackAgent",
                status="SUCCESS",
                note=f"{len(auto_replied)} auto-reply Slack notification(s) sent",
            )
        except Exception as exc:
            log_step(state, "AutoReplySlackAgent", "FAILED", str(exc))

        # Collect auto-replied cases into all_cases
        state.all_cases.extend(auto_replied)

    # Only escalated cases continue down the pipeline
    state.enriched_data = escalate_cases

    if not state.enriched_data:
        state.status = "SUCCESS"
        print("\n" + "=" * 60)
        print("  All cases handled via auto-reply — pipeline complete.")
        print(f"  WORKFLOW STATUS: {state.status}")
        print("=" * 60 + "\n")
        _flush_cost(state, _cost_tracker)
        return state

    # ── Step 5: Triage classification ─────────────────────
    # Only escalated cases (escalate_to_human=True) reach here.
    # Classify each by category, priority, sentiment, team.
    try:
        triage_cases = await run_triage_classification_agent(state.enriched_data)
        state.enriched_data = triage_cases
        log_step(
            state,
            agent="TriageClassificationAgent",
            status="SUCCESS",
            note=f"{len(triage_cases)} case(s) classified for human routing",
        )
    except Exception as exc:
        log_step(state, "TriageClassificationAgent", "FAILED", str(exc))
        state.status = "ESCALATE"
        _flush_cost(state, _cost_tracker)
        return state

    # ── Step 6+: Agents will be added here ────────────────
    # (route_ticket, notify_slack, reply_draft,
    #  approval, log_resolution)

    # ── Step 7: Jira ticket creation ──────────────────────
    try:
        jira_cases = await run_jira_agent(state.enriched_data)
        state.enriched_data = jira_cases
        log_step(
            state,
            agent="JiraAgent",
            status="SUCCESS",
            note=f"{len(jira_cases)} Jira ticket(s) created",
        )
    except Exception as exc:
        log_step(state, "JiraAgent", "FAILED", str(exc))
        state.status = "ESCALATE"
        _flush_cost(state, _cost_tracker)
        return state

    # ── Step 8: Slack notification ────────────────────────
    try:
        slack_cases = await run_notify_slack_agent(state.enriched_data)
        state.enriched_data = slack_cases
        log_step(
            state,
            agent="NotifySlackAgent",
            status="SUCCESS",
            note=f"{len(slack_cases)} Slack notification(s) sent",
        )
    except Exception as exc:
        log_step(state, "NotifySlackAgent", "FAILED", str(exc))
        state.status = "ESCALATE"
        _flush_cost(state, _cost_tracker)
        return state

    # ── Step 9: Reply email to customer ───────────────────
    # Only runs for cases where slack_result.status == "sent".
    try:
        reply_cases = await run_reply_agent(state.enriched_data)
        state.enriched_data = reply_cases
        sent_count = sum(
            1 for c in reply_cases
            if c.get("reply_result", {}).get("status") == "sent"
        )
        log_step(
            state,
            agent="ReplyAgent",
            status="SUCCESS",
            note=f"{sent_count}/{len(reply_cases)} reply email(s) sent to customers",
        )
    except Exception as exc:
        log_step(state, "ReplyAgent", "FAILED", str(exc))
        state.status = "ESCALATE"
        _flush_cost(state, _cost_tracker)
        return state

    # Collect fully-processed cases (went through entire pipeline)
    state.all_cases.extend(state.enriched_data)

    state.status = "SUCCESS"
    print("\n" + "=" * 60)
    print(f"  WORKFLOW STATUS: {state.status}")
    print("=" * 60 + "\n")
    _flush_cost(state, _cost_tracker)
    log_run_end(run_id, state.status, state.step_log)
    return state
