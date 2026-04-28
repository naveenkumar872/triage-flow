import os
import re
import json
import time
import asyncio
from typing import Any, Dict, List

from dotenv import load_dotenv
from llama_index.tools.mcp import BasicMCPClient, McpToolSpec
from llama_index.llms.google_genai import GoogleGenAI
from llama_index.core.agent import FunctionAgent
from utils.cost_tracker import get_tracker
from utils.agent_logger import log_agent_start, log_agent_end, log_agent_case, log_agent_error
from agents.prompts import get_confluence_system_prompt, build_confluence_user_prompt

# =========================================================
# CONFIG
# =========================================================
CONFIG_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "config")
load_dotenv(os.path.join(CONFIG_DIR, ".env"))

DATA_DIR   = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data")
OUTPUT_FILE = os.path.join(DATA_DIR, "confluence_results.json")

CONFLUENCE_API_KEY  = os.getenv("ATLASSIAN_CONFLUENCE_API_KEY")
CLOUD_ID            = os.getenv("ATLASSIAN_CLOUD_ID")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
ATLASSIAN_MCP_URL   = "https://mcp.atlassian.com/v1/mcp"

if not CONFLUENCE_API_KEY:
    raise RuntimeError("ATLASSIAN_CONFLUENCE_API_KEY is not set in config/.env")
if not CLOUD_ID:
    raise RuntimeError("ATLASSIAN_CLOUD_ID is not set in config/.env")



# MCP client — Confluence key has access to searchConfluenceUsingCql
print(f"[DEBUG] Connecting to MCP: {ATLASSIAN_MCP_URL}")
print(f"[DEBUG] Using Confluence API key: ...")
confluence_client = BasicMCPClient(
    ATLASSIAN_MCP_URL,
    headers={"Authorization": f"Bearer {CONFLUENCE_API_KEY}"},
)
confluence_spec = McpToolSpec(client=confluence_client)
print("[DEBUG] BasicMCPClient + McpToolSpec created (no network call yet)")

# =========================================================
# LLM
# =========================================================
llm = GoogleGenAI(
    model="gemini-2.5-flash",
    api_key=GEMINI_API_KEY,
)


# =========================================================
# SCHEMA SANITISER
# Gemini rejects additionalProperties in function declarations.
# Recursively strip it from MCP tool schemas before use.
# =========================================================
def remove_additional_properties(obj: Any) -> Any:
    if isinstance(obj, dict):
        return {
            k: remove_additional_properties(v)
            for k, v in obj.items()
            if k not in ("additionalProperties", "additional_properties")
        }
    if isinstance(obj, list):
        return [remove_additional_properties(i) for i in obj]
    return obj


def patch_tools_for_gemini(tools: list) -> list:
    """Patch each MCP tool's Pydantic schema so Gemini-incompatible fields are removed."""
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
        cleaned = remove_additional_properties(raw)
        # Patch model_json_schema (Pydantic v2) and schema (Pydantic v1 compat layer)
        # using a staticmethod wrapper so Pydantic's descriptor machinery is bypassed.
        _c = cleaned
        schema_cls.model_json_schema = classmethod(lambda cls, _c=_c, **kw: _c)
        try:
            schema_cls.schema = classmethod(lambda cls, _c=_c, **kw: _c)
        except (AttributeError, TypeError):
            pass  # schema() removed entirely in future Pydantic versions — safe to skip
    return tools


async def get_agent() -> FunctionAgent:
  

    print("[DEBUG] Calling confluence_spec.to_tool_list_async() ...")
    try:
        tools = await confluence_spec.to_tool_list_async()
    except Exception as e:
        print(f"[DEBUG] MCP tool fetch FAILED: {type(e).__name__}: {e}")
        raise
    print(f"[DEBUG] MCP returned {len(tools)} tool(s):")
    for t in tools:
        print(f"  - {t.metadata.name}: {(t.metadata.description or '')[:80]}")

    # Strip additionalProperties so Gemini doesn't reject the tool declarations
    tools = patch_tools_for_gemini(tools)
    print("[DEBUG] Tool schemas sanitised for Gemini")

    _agent = FunctionAgent(
        llm=llm,
        tools=tools,
        system_prompt=get_confluence_system_prompt(CLOUD_ID),
    )

    print(f"[DEBUG] FunctionAgent created. LLM type: {type(llm).__name__}")
    print("[ConfluenceSearchAgent] FunctionAgent initialised with Confluence MCP tools")
    return _agent




async def process_single_case(
    case: Dict[str, Any],
    agent: FunctionAgent,
) -> Dict[str, Any]:
    subject     = case.get("subject", "")
    body_text   = case.get("body_text", "")
    sender_name = case.get("sender_name", "Customer")

    print(f"\n  [Confluence] Processing: {subject[:60]}")

    prompt = build_confluence_user_prompt(sender_name, subject, body_text)

    # Run the FunctionAgent — it calls searchConfluenceUsingCql internally
    print(f"  [DEBUG] Sending prompt to agent (first 200 chars): {prompt[:200]}")
    t0 = time.perf_counter()
    try:
        response = await agent.run(prompt)
    except Exception as e:
        print(f"  [DEBUG] agent.run() FAILED: {type(e).__name__}: {e}")
        raise
    elapsed_ms = (time.perf_counter() - t0) * 1000

 
    tracker = get_tracker()
    if tracker is not None:
        prompt_tokens     = max(1, len(prompt) // 4)
        completion_tokens = 0
        if hasattr(response, "response"):
            raw_r = response.response
            if hasattr(raw_r, "content"):
                completion_tokens = max(1, len(str(raw_r.content)) // 4)
            else:
                completion_tokens = max(1, len(str(raw_r)) // 4)
        tracker.log(
            agent="ConfluenceSearchAgent",
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            duration_ms=elapsed_ms,
            gmail_id=case.get("gmail_id", ""),
        )

    resp_val = response.response if hasattr(response, "response") else response
    if hasattr(resp_val, "content"):
        raw = str(resp_val.content).strip()
    else:
        raw = str(resp_val).strip()
    print(f"  [DEBUG] Raw agent response (first 500 chars): {raw[:500]}")

    # Strip markdown code fences if the LLM added them
    raw = re.sub(r"^```(?:json)?\s*", "", raw)
    raw = re.sub(r"\s*```$",          "", raw)

    try:
        result = json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        result = {
            "escalate_to_human": True,
            "reply_msg":  "",
            "confidence": "low",
            "doc_title":  "",
            "doc_url":    "",
            "cql_used":   "",
        }

    # Enforce: escalating → reply_msg must be empty
    if result.get("escalate_to_human"):
        result["reply_msg"] = ""

    confluence_result = {
        "escalate_to_human": result.get("escalate_to_human", True),
        "reply_msg":         result.get("reply_msg",  ""),
        "confidence":        result.get("confidence", "low"),
        "doc_title":         result.get("doc_title",  ""),
        "doc_url":           result.get("doc_url",    ""),
        "cql_used":          result.get("cql_used",   ""),
        "docs_found":        1 if result.get("doc_title") else 0,
    }

    print(
        f"  [Confluence] escalate={confluence_result['escalate_to_human']} "
        f"confidence={confluence_result['confidence']}"
    )

    return {**case, "confluence_result": confluence_result}


async def run_confluence_search_agent(
    enriched_cases: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:

    print(f"\n[ConfluenceSearchAgent] Starting — {len(enriched_cases)} case(s)")

    log_agent_start("ConfluenceSearchAgent", {
        "cases_in": len(enriched_cases),
    })

    # Initialise agent once (fetches MCP tools async)
    agent = await get_agent()

    results = await asyncio.gather(
        *[process_single_case(case, agent) for case in enriched_cases]
    )
    output = list(results)

    os.makedirs(DATA_DIR, exist_ok=True)
    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2, ensure_ascii=False)

    for case in output:
        cr = case.get("confluence_result", {})
        log_agent_case(
            agent   = "ConfluenceSearchAgent",
            inputs  = {
                "subject":      case.get("subject", ""),
                "sender_email": case.get("sender_email", ""),
            },
            outputs = {
                "escalate_to_human": cr.get("escalate_to_human"),
                "confidence":        cr.get("confidence"),
                "doc_title":         cr.get("doc_title", ""),
                "doc_url":           cr.get("doc_url", ""),
            },
        )

    print(f"[ConfluenceSearchAgent] Done \u2014 saved to {OUTPUT_FILE}")

    log_agent_end("ConfluenceSearchAgent", {
        "cases_processed": len(output),
        "saved_to":        OUTPUT_FILE,
    })

    return output

