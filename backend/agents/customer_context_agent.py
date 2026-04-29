import os
import re
import json
import asyncio
from typing import Any, Dict, List, Optional, Tuple

from dotenv import load_dotenv
from llama_index.tools.mcp import BasicMCPClient, McpToolSpec
from llama_index.llms.google_genai import GoogleGenAI
from llama_index.core.agent import FunctionAgent
from llama_index.core.tools import FunctionTool
from utils.agent_logger import log_agent_start, log_agent_end, log_agent_case, log_agent_error
from utils.cost_tracker import get_tracker
from agents.prompts import DUPLICATE_SYSTEM_PROMPT

# =========================================================
# CONFIG
# =========================================================
CONFIG_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "config")
load_dotenv(os.path.join(CONFIG_DIR, ".env"))

DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data")
OUTPUT_FILE = os.path.join(DATA_DIR, "enriched_data.json")

ATLASSIAN_API_KEY  = os.getenv("ATLASSIAN_API_KEY")
ATLASSIAN_CLOUD_ID = os.getenv("ATLASSIAN_CLOUD_ID")
ATLASSIAN_MCP_URL  = "https://mcp.atlassian.com/v1/mcp"
DATABASE_MCP_URL   = "http://127.0.0.1:9000/mcp"

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

if not ATLASSIAN_API_KEY:
    raise RuntimeError("ATLASSIAN_API_KEY is not set in config/.env")
if not ATLASSIAN_CLOUD_ID:
    raise RuntimeError("ATLASSIAN_CLOUD_ID is not set in config/.env")

# LLM — used for semantic duplicate confirmation
_llm = GoogleGenAI(
    model="gemini-2.5-flash",
    api_key=GEMINI_API_KEY,
)

# MCP clients and specs — created once at module load
atlassian_client = BasicMCPClient(
    ATLASSIAN_MCP_URL,
    headers={"Authorization": f"Bearer {ATLASSIAN_API_KEY}"},
)
database_client = BasicMCPClient(DATABASE_MCP_URL)

atlassian_spec = McpToolSpec(client=atlassian_client)
database_spec  = McpToolSpec(client=database_client)


# =========================================================
# SCHEMA SANITISER (same pattern as confluence_search_agent)
# Gemini rejects additionalProperties in function declarations.
# =========================================================
def _remove_additional_properties(obj: Any) -> Any:
    if isinstance(obj, dict):
        return {
            k: _remove_additional_properties(v)
            for k, v in obj.items()
            if k not in ("additionalProperties", "additional_properties")
        }
    if isinstance(obj, list):
        return [_remove_additional_properties(i) for i in obj]
    return obj


def _patch_tools_for_gemini(tools: list) -> list:
    for tool in tools:
        schema_cls = getattr(tool.metadata, "fn_schema", None)
        if schema_cls is None:
            continue
        try:
            raw = schema_cls.model_json_schema()
        except Exception:
            try:
                raw = schema_cls.schema()
            except Exception:
                continue
        cleaned = _remove_additional_properties(raw)
        _c = cleaned
        schema_cls.model_json_schema = classmethod(lambda cls, _c=_c, **kw: _c)
        try:
            schema_cls.schema = classmethod(lambda cls, _c=_c, **kw: _c)
        except (AttributeError, TypeError):
            pass
    return tools

# ── Jira ID → English name maps (language-independent) ──────
# IDs never change regardless of Jira locale / language setting
ISSUE_TYPE_ID_MAP = {
    "10004": "Task",
    "10001": "Bug",
    "10002": "Story",
    "10003": "Epic",
    "10005": "Sub-task",
}

STATUS_ID_MAP = {
    "1":     "Open",
    "3":     "In Progress",
    "4":     "Reopened",
    "5":     "Resolved",
    "6":     "Closed",
    "10000": "To Do",
    "10001": "In Progress",
    "10002": "Done",
    "10014": "In Review",
}

PRIORITY_ID_MAP = {
    "1": "Highest",
    "2": "High",
    "3": "Medium",
    "4": "Low",
    "5": "Lowest",
}

# Status IDs treated as "open" for duplicate detection
# IDs are locale-safe — names like 打开/Open both have id="1"
OPEN_STATUS_IDS = {"1", "3", "4", "10000", "10001", "10014"}

# Noise words stripped before subject keyword extraction
STOP_WORDS = {
    "hi", "hello", "thanks", "bye", "the", "is", "was", "my", "your",
    "a", "an", "and", "or", "to", "in", "for", "of", "it", "i", "me",
    "we", "us", "be", "do", "not", "can", "could", "please", "help",
}


def _parse_mcp_raw(raw: str) -> Optional[Any]:
 
    import ast

    # Strategy C: text='...', annotations  (most reliable for TextContent reprs)
    m = re.search(r"text='(.*?)',\s*annotations", raw, re.DOTALL)
    if m:
        try:
            return json.loads(m.group(1))
        except (json.JSONDecodeError, TypeError):
            pass

    # Strategy A: text='{...}'  (fallback when no annotations field present)
    m2 = re.search(r"text='(\{[^']*\})'", raw)
    if m2:
        try:
            return json.loads(m2.group(1))
        except (json.JSONDecodeError, TypeError):
            pass

    # Strategy D: ast.literal_eval on structuredContent={...}
    m3 = re.search(r"structuredContent=(\{.*?\})\s+isError", raw, re.DOTALL)
    if m3:
        try:
            return ast.literal_eval(m3.group(1))
        except Exception:
            pass

    return None


def extract_text(result: Any) -> str:
    """Return the plain text/JSON string from an MCP tool result."""
    # Path 1: actual CallToolResult object with .content list
    if hasattr(result, "content"):
        content = result.content
        if isinstance(content, list):
            for item in content:
                if hasattr(item, "text") and isinstance(item.text, str):
                    return item.text
        if isinstance(content, str):
            return content

    # Path 2: result is already a plain string — apply all regex strategies
    raw = str(result)
    parsed = _parse_mcp_raw(raw)
    if parsed is not None:
        # Re-serialise so callers can do json.loads on the return value
        try:
            return json.dumps(parsed)
        except (TypeError, ValueError):
            pass

    return raw


async def call_tool(spec: McpToolSpec, tool_name: str, **kwargs) -> Any:
    tools = await spec.to_tool_list_async()
    target = next((t for t in tools if t.metadata.name == tool_name), None)
    if target is None:
        raise ValueError(f"Tool '{tool_name}' not found in MCP spec")
    result = await target.acall(**kwargs)

    # Already a dict — return directly
    if isinstance(result, dict):
        return result

    # structuredContent attribute is a non-empty dict
    if hasattr(result, "structuredContent"):
        sc = result.structuredContent
        if isinstance(sc, dict) and sc:
            return sc

    # Try the robust multi-strategy raw string parser first
    raw_str = str(result)
    parsed = _parse_mcp_raw(raw_str)
    if parsed is not None:
        return parsed

    # Fall back to extracting the text and JSON-parsing it
    text = extract_text(result)
    try:
        return json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return text


def parse_jira_issues(raw_text: str) -> List[Dict[str, Any]]:
    issues: List[Dict[str, Any]] = []

    # Locate every issue by its key position in the text
    key_positions = [
        (m.group(1), m.start())
        for m in re.finditer(r'"key"\s*:\s*"([A-Z]+-\d+)"', raw_text)
    ]

    for idx, (key, pos) in enumerate(key_positions):
        # Slice from this key to the start of the next key (or end of text)
        end   = key_positions[idx + 1][1] if idx + 1 < len(key_positions) else len(raw_text)
        chunk = raw_text[pos:end]

        # Summary
        sm = re.search(r'"summary"\s*:\s*"([^"]*)"', chunk)
        summary = sm.group(1) if sm else ""

        # Status ID — string-quoted id is the status id; int id is statusCategory
        st = re.search(r'"status"\s*:\s*\{.*?"id"\s*:\s*"(\d+)"', chunk, re.DOTALL)
        status_id   = st.group(1) if st else ""
        status_name = STATUS_ID_MAP.get(status_id, f"Unknown(id={status_id})")

        # Issue type ID
        it = re.search(r'"issuetype"\s*:\s*\{.*?"id"\s*:\s*"(\d+)"', chunk, re.DOTALL)
        type_id   = it.group(1) if it else ""
        type_name = ISSUE_TYPE_ID_MAP.get(type_id, f"Unknown(id={type_id})")

        # Priority ID
        pr = re.search(r'"priority"\s*:\s*\{.*?"id"\s*:\s*"(\d+)"', chunk, re.DOTALL)
        priority_id   = pr.group(1) if pr else ""
        priority_name = PRIORITY_ID_MAP.get(priority_id, f"Unknown(id={priority_id})")

        issues.append({
            "key":         key,
            "summary":     summary,
            "status_id":   status_id,
            "status":      status_name,
            "type_id":     type_id,
            "issue_type":  type_name,
            "priority_id": priority_id,
            "priority":    priority_name,
        })

    return issues


# =========================================================
# ======tools=====
# TOOL: _call_jira_search
# Calls searchJiraIssuesUsingJql, extracts raw text, and
# parses issues via regex + ID maps (no json.loads).
# Returns list of normalized issue dicts.
# =========================================================
async def call_jira_search(
    spec: McpToolSpec,
    jql: str,
    cloud_id: str,
    max_results: int = 5,
) -> List[Dict[str, Any]]:
    tools = await spec.to_tool_list_async()
    target = next((t for t in tools if t.metadata.name == "searchJiraIssuesUsingJql"), None)
    if target is None:
        raise ValueError("searchJiraIssuesUsingJql not found in Atlassian MCP spec")
    result   = await target.acall(jql=jql, cloudId=cloud_id, maxResults=max_results)
    raw_text = extract_text(result)
    return parse_jira_issues(raw_text)


# =========================================================
# ======tools=====
# TOOL: get_customer_profile
# Calls the database MCP tool 'get_customer_profile' by email.
# Returns: found (bool), profile dict (id, name, tier, company…)
# =========================================================
async def get_customer_profile(db_spec: McpToolSpec, email: str) -> Dict[str, Any]:
    try:
        result = await call_tool(db_spec, "get_customer_profile", email=email)
        if not isinstance(result, dict):
            return {"found": False, "profile": {}, "raw": str(result)}
        return result
    except Exception as exc:
        return {"found": False, "profile": {}, "error": str(exc)}


# =========================================================
# ======tools=====
# TOOL: get_ticket_history
# Calls the database MCP tool 'get_ticket_history' by customer_id.
# Returns: ticket_count (int), tickets (list of past ticket dicts)
# =========================================================
async def get_ticket_history(db_spec: McpToolSpec, customer_id: int) -> Dict[str, Any]:
    try:
        result = await call_tool(db_spec, "get_ticket_history", customer_id=customer_id)
        if not isinstance(result, dict):
            return {"ticket_count": 0, "tickets": [], "raw": str(result)}
        return result
    except Exception as exc:
        return {"ticket_count": 0, "tickets": [], "error": str(exc)}


# =========================================================
# ======tools=====
# TOOL: fetch_jira_strategy_1
# JQL Strategy 1 — subject keyword search.
# Extracts up to 3 meaningful keywords from the email subject
# and searches Jira issue summaries for matches.
# =========================================================
async def fetch_jira_strategy_1(
    atlassian_spec: McpToolSpec,
    subject: str,
    cloud_id: str,
) -> Dict[str, Any]:
    keywords = [
        w for w in re.sub(r"[^a-zA-Z0-9 ]", " ", subject).split()
        if w.lower() not in STOP_WORDS and len(w) > 2
    ]
    if not keywords:
        return {"strategy": "1", "jql": None, "issues": [], "skipped": True}

    jql = " OR ".join(f'summary ~ "{kw}"' for kw in keywords[:3]) + " ORDER BY created DESC"
    try:
        issues = await call_jira_search(atlassian_spec, jql, cloud_id)
    except Exception as exc:
        issues = []
        print(f"  [!] Strategy 1 JQL error: {exc}")

    return {"strategy": "1", "jql": jql, "issues": issues, "skipped": False}


# =========================================================
# DUPLICATE-CHECK AGENT
# A FunctionAgent with a FunctionTool wrapping call_jira_search.
# spec and cloud_id are baked in — the LLM only supplies jql.
# =========================================================

async def _jira_search_tool_fn(jql: str, max_results: int = 5) -> str:
    """
    Search Jira for issues using a JQL query.
    Returns a JSON array of matching issues, each with key, summary, status, and priority.
    Use concise, topic-focused JQL — e.g.: summary ~ "login error" AND statusCategory != Done
    """
    try:
        issues = await call_jira_search(atlassian_spec, jql, ATLASSIAN_CLOUD_ID, max_results)
        return json.dumps(issues)
    except Exception as exc:
        return json.dumps({"error": str(exc), "issues": []})


jira_search_tool = FunctionTool.from_defaults(
    async_fn=_jira_search_tool_fn,
    name="search_jira_issues",
    description=(
        "Search Jira for existing tickets using a JQL query. "
        "Use this to find open tickets that may describe the same problem as the incoming email. "
        "Pass a concise JQL string (e.g. 'summary ~ \"payment error\" AND statusCategory != Done'). "
        "Returns a JSON array of matching issues with key, summary, status, and priority."
    ),
)





def _build_duplicate_agent() -> FunctionAgent:
    """Build a FunctionAgent with the Jira search FunctionTool."""
    return FunctionAgent(
        name="DuplicateCheckAgent",
        description="Checks whether an incoming email is a duplicate of an existing open Jira ticket.",
        llm=_llm,
        tools=[jira_search_tool],
        system_prompt=DUPLICATE_SYSTEM_PROMPT,
    )


# =========================================================
# ======tools=====
# TOOL: check_duplicate
# Runs a FunctionAgent that has searchJiraIssuesUsingJql
# attached.  The agent decides autonomously what to search
# and whether the result is a true duplicate.
# =========================================================
async def check_duplicate(
    atlassian_spec: McpToolSpec,
    sender_email: str,
    subject: str,
    cloud_id: str,
    body_text: str = "",
    gmail_id: str = "",
) -> Dict[str, Any]:
    user_msg = (
        f"New support email:\n"
        f"Subject: {subject}\n"
        f"Body (first 400 chars): {body_text[:400]}\n\n"
        f"Cloud ID for Jira search: {cloud_id}\n"
        f"Search for any OPEN Jira ticket that describes the SAME problem."
    )

    try:
        import time as _time
        agent = _build_duplicate_agent()
        _t0   = _time.perf_counter()
        result = await agent.run(user_msg)
        _elapsed_ms = (_time.perf_counter() - _t0) * 1000
        raw    = result.response if hasattr(result, "response") else result
        raw    = str(raw.content if hasattr(raw, "content") else raw).strip()
        print(f"  [DuplicateAgent] raw response: {raw[:300]}")

        # Track LLM usage
        _tracker = get_tracker()
        if _tracker is not None:
            _prompt_tokens     = max(1, len(user_msg) // 4)
            _completion_tokens = max(1, len(raw) // 4)
            _tracker.log(
                agent="CustomerContextAgent",
                prompt_tokens=_prompt_tokens,
                completion_tokens=_completion_tokens,
                duration_ms=_elapsed_ms,
                gmail_id=gmail_id,
            )

        # Strip markdown fencesstrategiesrun
        raw = re.sub(r"^```(?:json)?\s*", "", raw)
        raw = re.sub(r"\s*```$", "", raw)
        data = json.loads(raw)

        is_dup  = bool(data.get("is_duplicate", False))
        mkey    = data.get("matched_key") or None
        msum    = data.get("matched_summary") or ""

        matched_issues = []
        if is_dup and mkey:
            matched_issues.append({
                "key":      mkey,
                "summary":  msum,
                "strategy": "agent",
            })

        return {
            "is_duplicate":            is_dup,
            "duplicate_count":         len(matched_issues),
            "matched_issues":          matched_issues,
            "first_match_strategy":    "agent" if is_dup else None,
            "first_match_full_result": matched_issues[0] if matched_issues else None,
            "strategies_run":          ["agent"],
        }

    except Exception as exc:
        print(f"  [!] DuplicateAgent failed: {exc}")
        # Safe fallback — no duplicate flagged
        return {
            "is_duplicate":            False,
            "duplicate_count":         0,
            "matched_issues":          [],
            "first_match_strategy":    None,
            "first_match_full_result": None,
            "strategies_run":          ["agent"],
            "error":                   str(exc),
        }


# =========================================================
# ======tools=====
# TOOL: enrich_single_case
# Enriches one intake case dict.
#
# Execution model per case:
#   ┌─ DB group (sequential) ────────────────────────────┐
#   │  get_customer_profile(email)                       │  ─┐
#   │    └─► get_ticket_history(customer_id)             │   │ both groups
#   └────────────────────────────────────────────────────┘   │ run concurrently
#   ┌─ Jira group (parallel) ────────────────────────────┐   │
#   │  strategy 1 ──┬──► check_duplicate result          │  ─┘
#   │  strategy 2 ──┘                                    │
#   └────────────────────────────────────────────────────┘
# =========================================================
async def enrich_single_case(
    case: Dict[str, Any],
    db_spec: McpToolSpec,
    atlassian_spec: McpToolSpec,
    cloud_id: str,
) -> Dict[str, Any]:
    sender_email: str = case.get("sender_email", "")
    subject: str      = case.get("subject", "")

    # DB group — sequential (history needs customer_id from profile)
    async def db_group() -> Tuple[Dict[str, Any], Dict[str, Any]]:
        profile = await get_customer_profile(db_spec, sender_email)
        customer_id: Optional[int] = None
        if isinstance(profile, dict) and profile.get("found"):
            customer_id = profile["profile"]["id"]
        history = (
            await get_ticket_history(db_spec, customer_id)
            if customer_id is not None
            else {"ticket_count": 0, "tickets": [], "message": "No customer_id — skipped"}
        )
        return profile, history

    # Jira group — FunctionAgent with searchJiraIssuesUsingJql
    async def jira_group() -> Dict[str, Any]:
        return await check_duplicate(
            atlassian_spec, sender_email, subject, cloud_id,
            body_text=case.get("body_text", ""),
            gmail_id=case.get("gmail_id", ""),
        )

    # Run both groups concurrently
    (profile_result, history_result), duplicate_result = await asyncio.gather(
        db_group(),
        jira_group(),
    )

    _pf = profile_result if isinstance(profile_result, dict) else {}
    _hr = history_result if isinstance(history_result, dict) else {}
    print(
        f"  [case] {sender_email[:30]:<30} | "
        f"profile={'✓' if _pf.get('found') else '✗'} | "
        f"tickets={_hr.get('ticket_count', 0)} | "
        f"duplicate={'YES' if duplicate_result['is_duplicate'] else 'no'}"
    )

    # ── Build flat customer_context for downstream agents ──────
    # triage_classification_agent and jira_agent both read:
    #   case["customer_context"]["tier"]
    #   case["customer_context"]["open_issue_count"]
    #   case["customer_context"]["total_past_issues"]
    #   case["customer_context"]["account_age_days"]
    _profile_data = _pf.get("profile", {}) if _pf.get("found") else {}
    _tickets      = _hr.get("tickets", [])
    _open_statuses = {"open", "in_progress", "pending", "active", "reopened"}
    _open_count   = sum(
        1 for t in _tickets
        if str(t.get("status", "")).lower().replace(" ", "_") in _open_statuses
    )

    # account_age_days: parse account_created_at from profile
    _account_age: Any = "?"
    _created_at_str = _profile_data.get("account_created_at") or _profile_data.get("created_at")
    if _created_at_str:
        try:
            from datetime import datetime, timezone
            _created = datetime.strptime(str(_created_at_str)[:19], "%Y-%m-%d %H:%M:%S")
            _account_age = (datetime.now() - _created).days
        except (ValueError, TypeError):
            pass

    customer_context = {
        "tier":              _profile_data.get("tier", "unknown"),
        "name":              _profile_data.get("name", ""),
        "company":           _profile_data.get("company", ""),
        "open_issue_count":  _open_count,
        "total_past_issues": _hr.get("ticket_count", 0),
        "account_age_days":  _account_age,
    }

    return {
        **case,
        "customer_profile": profile_result,
        "ticket_history":   history_result,
        "duplicate_check":  duplicate_result,
        "customer_context": customer_context,
    }


async def run_customer_context_agent(
    intake_cases: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    print(f"\n[CustomerContextAgent] Enriching {len(intake_cases)} case(s)...")

    log_agent_start("CustomerContextAgent", {
        "cases_in": len(intake_cases),
    })

    tasks = [
        enrich_single_case(case, database_spec, atlassian_spec, ATLASSIAN_CLOUD_ID)
        for case in intake_cases
    ]
    enriched_cases: List[Dict[str, Any]] = await asyncio.gather(*tasks)

    os.makedirs(DATA_DIR, exist_ok=True)
    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(enriched_cases, f, indent=2, default=str)

    duplicate_count = sum(1 for c in enriched_cases if c["duplicate_check"]["is_duplicate"])
    print(f"[CustomerContextAgent] {len(enriched_cases)} case(s) enriched | {duplicate_count} duplicate(s) found")
    print(f"[CustomerContextAgent] Saved -> {OUTPUT_FILE}")

    for case in enriched_cases:
        log_agent_case(
            agent   = "CustomerContextAgent",
            inputs  = {
                "subject":      case.get("subject", ""),
                "sender_email": case.get("sender_email", ""),
            },
            outputs = {
                "has_profile":   bool(case.get("customer_profile")),
                "ticket_count":  len(case.get("ticket_history") or []),
                "is_duplicate":  case.get("duplicate_check", {}).get("is_duplicate", False),
            },
        )

    log_agent_end("CustomerContextAgent", {
        "cases_enriched": len(enriched_cases),
        "duplicates":     duplicate_count,
        "saved_to":       OUTPUT_FILE,
    })

    return enriched_cases



