import os
import re
import json
import asyncio
from typing import Any, Dict, List

from dotenv import load_dotenv
from llama_index.tools.mcp import BasicMCPClient, McpToolSpec
from utils.agent_logger import log_agent_start, log_agent_end, log_agent_case, log_agent_error

# =========================================================
# CONFIG
# =========================================================
CONFIG_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "config")
load_dotenv(os.path.join(CONFIG_DIR, ".env"))

_DATA_DIR   = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data")
OUTPUT_FILE = os.path.join(_DATA_DIR, "jira_results.json")

_JIRA_API_KEY      = os.getenv("ATLASSIAN_API_KEY")
_CLOUD_ID          = os.getenv("ATLASSIAN_CLOUD_ID")
ATLASSIAN_MCP_URL = "https://mcp.atlassian.com/v1/mcp"
_PROJECT_KEY       = os.getenv("JIRA_PROJECT_KEY", "SCRUM")   # override in .env if needed

if not _JIRA_API_KEY:
    raise RuntimeError("ATLASSIAN_API_KEY is not set in config/.env")
if not _CLOUD_ID:
    raise RuntimeError("ATLASSIAN_CLOUD_ID is not set in config/.env")

# =========================================================
# MAPPINGS
# =========================================================
# triage_result.category → Jira issue type
# NOTE: RA project only has "Task" configured.
_ISSUE_TYPE_MAP: Dict[str, str] = {
    "technical":       "Task",
    "data":            "Task",
    "billing":         "Task",
    "account":         "Task",
    "feature_request": "Task",
    "general":         "Task",
}

# triage_result.priority → Jira priority name
_JIRA_PRIORITY_MAP: Dict[str, str] = {
    "P1": "Highest",
    "P2": "High",
    "P3": "Medium",
    "P4": "Low",
}


async def get_create_jira_tool():
    client = BasicMCPClient(
        ATLASSIAN_MCP_URL,
        headers={"Authorization": f"Bearer {_JIRA_API_KEY}"},
    )
    spec  = McpToolSpec(client=client)
    tools = await spec.to_tool_list_async()

    for tool in tools:
        if tool.metadata.name == "createJiraIssue":
            return tool

    available = [t.metadata.name for t in tools]
    raise RuntimeError(
        f"createJiraIssue not found in MCP tools.\nAvailable: {available}"
    )


def build_description(case: Dict[str, Any]) -> str:
    body_text   = case.get("body_text", "")
    sender_name = case.get("sender_name", "Unknown")
    sender_email = case.get("sender_email", "Unknown")
    subject     = case.get("subject", "")

    ctx = case.get("customer_context", {})
    tr  = case.get("triage_result", {})
    cr  = case.get("confluence_result", {})

    lines = [
        f"**Subject:** {subject}",
        f"**From:** {sender_name} <{sender_email}>",
        "",
        "---",
        "**Email Body:**",
        body_text,
        "",
        "---",
        "**Customer Context:**",
        f"- Tier: {ctx.get('tier', 'unknown')}",
        f"- Open issues: {ctx.get('open_issue_count', 0)}",
        f"- Total past issues: {ctx.get('total_past_issues', 0)}",
        f"- Account age: {ctx.get('account_age_days', '?')} days",
        "",
        "---",
        "**Triage Classification:**",
        f"- Category: {tr.get('category', '')}",
        f"- Priority: {tr.get('priority', '')}",
        f"- Sentiment: {tr.get('sentiment', '')}",
        f"- Suggested team: {tr.get('suggested_team', '')}",
        f"- Tags: {', '.join(tr.get('tags', []))}",
        "",
        "---",
        "**Confluence Search (auto-reply attempted, failed):**",
        f"- Docs found: {cr.get('docs_found', 0)}",
        f"- Best doc: {cr.get('doc_title', 'none')}",
        f"- Confidence: {cr.get('confidence', 'low')}",
    ]
    return "\n".join(lines)



async def create_single_ticket(
    case: Dict[str, Any],
    create_tool,
) -> Dict[str, Any]:
    tr      = case.get("triage_result", {})
    subject = case.get("subject", "No subject")

    summary       = tr.get("summary") or subject
    category      = tr.get("category", "general")
    priority      = tr.get("priority", "P3")

    issue_type    = _ISSUE_TYPE_MAP.get(category, "Task")
    jira_priority = _JIRA_PRIORITY_MAP.get(priority, "Medium")
    description   = build_description(case)

    print(f"\n  [Jira] Creating ticket: {summary[:60]}")
    print(f"         type={issue_type}  priority={jira_priority}  project={_PROJECT_KEY}")

    try:
        result = await create_tool.acall(
            cloudId       = _CLOUD_ID,
            projectKey    = _PROJECT_KEY,
            issueTypeName = issue_type,
            summary       = summary,
            description   = description,
            contentFormat = "markdown",
        )

        raw = str(result)
        print(f"  [Jira] Raw response: {raw[:300]}")

        # Detect MCP-level error flag
        if "isError=True" in raw:
            # Extract the error message for logging
            err_match = re.search(r'"message":\s*"([^"]+)"', raw)
            err_msg   = err_match.group(1) if err_match else raw
            print(f"  [Jira] MCP returned isError=True: {err_msg}")
            raise RuntimeError(f"Jira MCP error: {err_msg}")

        # Parse response to extract ticket key/id/url
        ticket_key = ""
        ticket_id  = ""
        ticket_url = ""

        try:
            # MCP wraps the response as ToolOutput; the actual JSON is in content[0].text
            # Try to pull the inner JSON string from text='...'
            text_match = re.search(r"text='(\{.*?\})'", raw, re.DOTALL)
            json_str   = text_match.group(1) if text_match else raw
            data = json.loads(json_str)
            if isinstance(data, dict):
                ticket_key = data.get("key", "")
                ticket_id  = data.get("id", "")
                self_url   = data.get("self", "")
                if ticket_key and _CLOUD_ID:
                    base = _CLOUD_ID.rstrip("/")
                    ticket_url = f"{base}/browse/{ticket_key}"
                elif self_url:
                    ticket_url = self_url
        except (json.JSONDecodeError, TypeError, AttributeError):
            # Fallback: scan for ticket key pattern like RA-42
            m = re.search(r"([A-Z]+-\d+)", raw)
            if m:
                ticket_key = m.group(1)
                base       = _CLOUD_ID.rstrip("/")
                ticket_url = f"{base}/browse/{ticket_key}"

        jira_result = {
            "ticket_key": ticket_key,
            "ticket_id":  ticket_id,
            "ticket_url": ticket_url,
            "issue_type": issue_type,
            "priority":   jira_priority,
            "status":     "created",
            "success":    bool(ticket_key),
            "raw":        raw,
        }

        print(f"  [Jira] Ticket created: {ticket_key or 'unknown key'}  {ticket_url}")

    except Exception as exc:
        print(f"  [Jira] ERROR creating ticket: {type(exc).__name__}: {exc}")
        jira_result = {
            "ticket_key": "",
            "ticket_id":  "",
            "ticket_url": "",
            "issue_type": issue_type,
            "priority":   jira_priority,
            "status":     "error",
            "success":    False,
            "error":      str(exc),
        }

    case_out = dict(case)
    case_out["jira_result"] = jira_result
    return case_out


async def run_jira_agent(triage_cases: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    print("\n" + "=" * 60)
    print("  JIRA AGENT — Creating tickets")
    print(f"  Cases to process: {len(triage_cases)}")
    print("=" * 60)

    log_agent_start("JiraAgent", {
        "cases_in": len(triage_cases),
    })

    # Fetch the tool once — reuse across all cases
    print("  [Jira] Connecting to Atlassian MCP...")
    create_tool = await get_create_jira_tool()
    print("  [Jira] createJiraIssue tool ready")

    # Create tickets sequentially to avoid rate-limits on the MCP API
    results: List[Dict[str, Any]] = []
    for case in triage_cases:
        result = await create_single_ticket(case, create_tool)
        results.append(result)
        jr = result.get("jira_result", {})
        log_agent_case(
            agent   = "JiraAgent",
            inputs  = {
                "subject":      case.get("subject", ""),
                "sender_email": case.get("sender_email", ""),
                "priority":     case.get("triage_result", {}).get("priority", ""),
            },
            outputs = {
                "ticket_key": jr.get("ticket_key", ""),
                "ticket_url": jr.get("ticket_url", ""),
                "success":    jr.get("success", False),
            },
        )

    # Save
    os.makedirs(_DATA_DIR, exist_ok=True)
    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2, default=str)
    print(f"\n  [Jira] Results saved -> {OUTPUT_FILE}")

    log_agent_end("JiraAgent", {
        "tickets_created": len(results),
        "saved_to":        OUTPUT_FILE,
    })

    return results

