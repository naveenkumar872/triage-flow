import os
import re
import json
import asyncio
from typing import Any, Dict, List, Optional

from dotenv import load_dotenv
from llama_index.tools.mcp import BasicMCPClient, McpToolSpec
from utils.agent_logger import log_agent_start, log_agent_end, log_agent_case, log_agent_error

# =========================================================
# CONFIG
# =========================================================
CONFIG_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "config")
load_dotenv(os.path.join(CONFIG_DIR, ".env"))

DATA_DIR   = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data")
OUTPUT_FILE = os.path.join(DATA_DIR, "slack_results.json")

SLACK_MCP_URL     = "http://127.0.0.1:9000/mcp"
ATLASSIAN_CLOUD_ID = os.getenv("ATLASSIAN_CLOUD_ID", "").rstrip("/")

# Local server uses SLACK_BOT_TOKEN — no auth header needed

# =========================================================
# TEAM → CHANNEL SEARCH QUERY MAP
# Maps triage_result.suggested_team → keyword to search
# for the matching Slack channel via slack_search_channels.
# =========================================================
TEAM_CHANNEL_QUERY: Dict[str, str] = {
    "engineering":       "engineering",
    "billing":           "billing",
    "account_management": "account",
    "support":           "support",
    "security":          "security",
}
DEFAULT_CHANNEL_QUERY = "general"


# =========================================================
# ======tools=====
# TOOL: get_slack_tools
# Connects to the Slack MCP and returns
# (search_channels_tool, send_message_tool).
# =========================================================
async def get_slack_tools():
    client = BasicMCPClient(SLACK_MCP_URL)
    spec  = McpToolSpec(client=client)
    tools = await spec.to_tool_list_async()

    tool_map = {t.metadata.name: t for t in tools}

    search_tool = tool_map.get("slack_search_channels")
    send_tool   = tool_map.get("send_bot_message")

    missing = [
        name for name, t in [
            ("slack_search_channels", search_tool),
            ("send_bot_message",      send_tool),
        ] if t is None
    ]
    if missing:
        available = list(tool_map.keys())
        raise RuntimeError(
            f"Missing Slack MCP tools: {missing}\nAvailable: {available}"
        )

    return search_tool, send_tool


# =========================================================
# ======tools=====
# TOOL: find_channel_id
# Calls slack_search_channels with the team keyword and
# returns the first matching channel_id (or None).
# =========================================================
async def find_channel_id(
    search_tool,
    query: str,
) -> Optional[str]:
    print(f"  [Slack] Searching channels: query='{query}'")
    try:
        result = await search_tool.acall(query=query)
        raw = str(result)
        print(f"  [Slack] Search raw: {raw}")

        # Extract inner text from MCP ToolOutput wrapper
        text_match = re.search(r"text='(\{.*?\})'", raw, re.DOTALL)
        json_str   = text_match.group(1) if text_match else raw

        # Unescape the JSON string (\\n → \n, \\/ → /)
        json_str = json_str.replace("\\/", "/")

        data = json.loads(json_str)

        # Local server returns a plain dict — channels is already a list
        channels = data.get("channels", [])
        if channels:
            ch = channels[0]
            ch_name = ch.get("name", "unknown")
            ch_id   = ch.get("id", "")
            print(f"  [Slack] Found channel: #{ch_name}  ({ch_id})")
            return ch_id

        print(f"  [Slack] Could not extract channel ID from response")

    except Exception as exc:
        print(f"  [Slack] Channel search failed: {type(exc).__name__}: {exc}")

    return None


def build_slack_message(case: Dict[str, Any]) -> str:
    tr = case.get("triage_result", {})
    jr = case.get("jira_result", {})

    subject    = case.get("subject", "No subject")
    sender     = case.get("sender_name", "Unknown")
    email      = case.get("sender_email", "")
    priority   = tr.get("priority", "?")
    category   = tr.get("category", "?")
    sentiment  = tr.get("sentiment", "?")
    summary    = tr.get("summary", subject)
    team       = tr.get("suggested_team", "?")
    ticket_key = jr.get("ticket_key", "")
    ticket_url = jr.get("ticket_url", "")

    # Priority emoji
    priority_emoji = {
        "P1": ":red_circle:",
        "P2": ":large_orange_circle:",
        "P3": ":large_yellow_circle:",
        "P4": ":white_circle:",
    }.get(priority, ":white_circle:")

    jira_line = (
        f"*Jira ticket:* <{ticket_url}|{ticket_key}>"
        if ticket_key and ticket_url
        else "*Jira ticket:* not created"
    )

    return (
        f"{priority_emoji} *New escalated case — {priority} / {category}*\n"
        f"*From:* {sender} <{email}>\n"
        f"*Subject:* {subject}\n"
        f"*Summary:* {summary}\n"
        f"*Sentiment:* {sentiment}   *Assigned to:* {team}\n"
        f"{jira_line}"
    )



def build_auto_reply_slack_message(case: Dict[str, Any]) -> str:
    subject    = case.get("subject", "No subject")
    sender     = case.get("sender_name", "Unknown")
    email      = case.get("sender_email", "")

    cr         = case.get("confluence_result", {})
    doc_title  = cr.get("doc_title", "N/A")
    doc_url    = cr.get("doc_url", "")
    confidence = cr.get("confidence", "N/A")
    reply_msg  = cr.get("reply_msg", "")

    reply_result = case.get("reply_result", {})
    reply_status = reply_result.get("status", "pending")
    msg_id       = reply_result.get("message_id", "")

    doc_line     = f"<{doc_url}|{doc_title}>" if doc_url else doc_title
    status_emoji = ":white_check_mark:" if reply_status == "sent" else ":warning:"

    # Use the actual LLM-drafted reply sent to the customer (stored in draft),
    # falling back to the raw KB answer if the draft isn't available.
    sent_reply = reply_result.get("draft") or reply_msg or "(no reply body)"

    body_text = case.get("body_text", "").strip()

    return (
        f":outbox_tray: *Issue Resolved Using Confluence Documents \u2014 Auto-Reply Sent*\n\n"
        f"*Customer:* {sender} <{email}>\n"
        f"*Subject:* {subject}\n\n"
        f"*Customer email:*\n```{body_text}```\n\n"
        f"After checking the Confluence knowledge base, this was identified as a "
        f"*frequently asked question / already resolved problem* "
        f"(source: {doc_line}, confidence: *{confidence}*), "
        f"so the auto-reply agent got triggered and sent the below message to the customer:\n\n"
        f"```{sent_reply}```\n\n"
        f"*Email status:* {status_emoji} {reply_status}"
        + (f"  (id: `{msg_id}`)" if msg_id else "")
        + f"\n_If the customer replies, a new case will be created._"
    )


async def notify_single_case(
    case: Dict[str, Any],
    search_tool,
    send_tool,
) -> Dict[str, Any]:
    tr            = case.get("triage_result", {})
    suggested_team = tr.get("suggested_team", "support")
    subject       = case.get("subject", "No subject")

    # Determine channel search query
    query = TEAM_CHANNEL_QUERY.get(suggested_team, DEFAULT_CHANNEL_QUERY)

    print(f"\n  [Slack] Notifying for: {subject[:60]}")
    print(f"         team={suggested_team}  channel_query='{query}'")

    # Step 1: find channel id
    channel_id = await find_channel_id(search_tool, query)

    if not channel_id:
        print(f"  [Slack] No channel found for '{query}' — skipping send")
        case_out = dict(case)
        case_out["slack_result"] = {
            "status":     "no_channel",
            "channel_id": "",
            "message_ts": "",
            "error":      f"No channel found for query='{query}'",
        }
        return case_out

    # Step 2: send message
    message = build_slack_message(case)
    print(f"  [Slack] Sending to channel {channel_id}...")

    try:
        result = await send_tool.acall(
            channel_id = channel_id,
            text       = message,
        )
        raw = str(result)
        print(f"  [Slack] Send raw: {raw[:300]}")

        # Local server returns plain dict wrapped in ToolOutput
        text_match = re.search(r"text='(\{.*?\})'", raw, re.DOTALL)
        json_str   = text_match.group(1) if text_match else raw

        message_ts   = ""
        message_link = ""
        try:
            data = json.loads(json_str)
            if isinstance(data, dict):
                message_ts   = data.get("ts", data.get("message_ts", ""))
                message_link = data.get("message_link", data.get("link", ""))
        except (json.JSONDecodeError, TypeError):
            pass

        print(f"  [Slack] Message sent: ts={message_ts}  link={message_link}")

        slack_result = {
            "status":       "sent",
            "channel_id":   channel_id,
            "message_ts":   message_ts,
            "message_link": message_link,
        }

    except Exception as exc:
        print(f"  [Slack] ERROR sending message: {type(exc).__name__}: {exc}")
        slack_result = {
            "status":     "error",
            "channel_id": channel_id,
            "message_ts": "",
            "error":      str(exc),
        }

    case_out = dict(case)
    case_out["slack_result"] = slack_result
    return case_out


async def run_notify_slack_agent(
    jira_cases: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    print("\n" + "=" * 60)
    print("  NOTIFY SLACK AGENT — Sending team notifications")
    print(f"  Cases to process: {len(jira_cases)}")
    print("=" * 60)

    print("  [Slack] Connecting to local Slack MCP server...")
    search_tool, send_tool = await get_slack_tools()
    print("  [Slack] slack_search_channels + send_bot_message ready")


    log_agent_start("NotifySlackAgent", {"cases_in": len(jira_cases)})
    results: List[Dict[str, Any]] = []
    for case in jira_cases:
        result = await notify_single_case(case, search_tool, send_tool)
        results.append(result)
        sr = result.get("slack_result", {})
        log_agent_case(
            agent   = "NotifySlackAgent",
            inputs  = {"subject": case.get("subject", ""), "sender_email": case.get("sender_email", "")},
            outputs = {"status": sr.get("status", ""), "channel_id": sr.get("channel_id", "")},
        )

    # Save
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2, default=str)
    print(f"\n  [Slack] Results saved -> {OUTPUT_FILE}")


    log_agent_end("NotifySlackAgent", {"cases_notified": len(results), "saved_to": OUTPUT_FILE})
    return results


async def run_notify_slack_auto_reply(
    auto_reply_cases: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    print("\n" + "=" * 60)
    print("  NOTIFY SLACK AGENT — Auto-reply notifications")
    print(f"  Cases to notify: {len(auto_reply_cases)}")
    print("=" * 60)

    print("  [Slack] Connecting to local Slack MCP server...")
    search_tool, send_tool = await get_slack_tools()
    print("  [Slack] slack_search_channels + send_bot_message ready")

    log_agent_start("NotifySlackAutoReply", {"cases_in": len(auto_reply_cases)})

    results: List[Dict[str, Any]] = []
    for case in auto_reply_cases:
        subject = case.get("subject", "No subject")
        print(f"\n  [Slack] Auto-reply notification for: {subject[:60]}")

        # Auto-replies go to the general/support channel
        channel_id = await find_channel_id(search_tool, DEFAULT_CHANNEL_QUERY)

        case_out = dict(case)
        if not channel_id:
            print(f"  [Slack] No channel found — skipping notification")
            case_out["auto_reply_slack_result"] = {
                "status":     "no_channel",
                "channel_id": "",
                "error":      f"No channel found for query='{DEFAULT_CHANNEL_QUERY}'",
            }
            results.append(case_out)
            continue

        message = build_auto_reply_slack_message(case)
        print(f"  [Slack] Sending to channel {channel_id}...")

        try:
            result = await send_tool.acall(channel_id=channel_id, text=message)
            raw = str(result)
            print(f"  [Slack] Send raw: {raw[:300]}")

            text_match = re.search(r"text='(\{.*?\})'", raw, re.DOTALL)
            json_str   = text_match.group(1) if text_match else raw

            message_ts = ""
            try:
                data = json.loads(json_str)
                if isinstance(data, dict):
                    message_ts = data.get("ts", data.get("message_ts", ""))
            except (json.JSONDecodeError, TypeError):
                pass

            print(f"  [Slack] Auto-reply notification sent: ts={message_ts}")
            case_out["auto_reply_slack_result"] = {
                "status":     "sent",
                "channel_id": channel_id,
                "message_ts": message_ts,
            }
        except Exception as exc:
            print(f"  [Slack] ERROR sending auto-reply notification: {type(exc).__name__}: {exc}")
            case_out["auto_reply_slack_result"] = {
                "status":     "error",
                "channel_id": channel_id,
                "error":      str(exc),
            }

        results.append(case_out)
        sr = case_out.get("auto_reply_slack_result", {})
        log_agent_case(
            agent   = "NotifySlackAutoReply",
            inputs  = {"subject": case.get("subject", ""), "sender_email": case.get("sender_email", "")},
            outputs = {"status": sr.get("status", ""), "channel_id": sr.get("channel_id", "")},
        )

    log_agent_end("NotifySlackAutoReply", {"cases_notified": len(results)})

    return results


def build_duplicate_slack_message(case: Dict[str, Any]) -> str:
    subject  = case.get("subject", "No subject")
    sender   = case.get("sender_name", "Unknown")
    email    = case.get("sender_email", "")

    dup             = case.get("duplicate_check", {})
    matched         = dup.get("matched_issues", [])
    first           = matched[0] if matched else {}
    ticket_key      = first.get("key", "N/A")
    ticket_status   = first.get("status", "Unknown")
    ticket_priority = first.get("priority", "?")
    ticket_summary  = first.get("summary", subject)

    reply_result = case.get("reply_result", {})
    reply_status = reply_result.get("status", "pending")
    reply_emoji  = ":white_check_mark:" if reply_status == "sent" else ":warning:"

    ticket_line = (
        f"<{ATLASSIAN_CLOUD_ID}/browse/{ticket_key}|{ticket_key}>"
        if ATLASSIAN_CLOUD_ID and ticket_key != "N/A"
        else ticket_key
    )

    matched_list = "\n".join(
        f"  → {m.get('key')} | {m.get('status')} | "
        f"{m.get('priority')} | {m.get('summary', '')}"
        for m in matched
    )

    return (
        f":repeat: *Existing Ticket Found in Jira \u2014 Auto-Reply Sent to Customer*\n\n"
        f"*Customer:* {sender} <{email}>\n"
        f"*Subject:* {subject}\n\n"
        f"An open Jira ticket already exists for this issue:\n"
        f"*{ticket_line}* \u2014 {ticket_summary} "
        f"(status: {ticket_status}, priority: {ticket_priority})\n\n"
        f"*All matched tickets ({len(matched)}):*\n{matched_list}\n\n"
        f"*Auto-reply sent to customer:* {reply_emoji} {reply_status}\n"
        f"_No new Jira ticket was created. Customer has been informed their case is already being tracked._"
    )


async def run_notify_slack_duplicate(
    duplicate_cases: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    print("\n" + "=" * 60)
    print("  NOTIFY SLACK AGENT — Duplicate case notifications")
    print(f"  Cases to notify: {len(duplicate_cases)}")
    print("=" * 60)

    print("  [Slack] Connecting to local Slack MCP server...")
    search_tool, send_tool = await get_slack_tools()
    print("  [Slack] slack_search_channels + send_bot_message ready")

    log_agent_start("NotifySlackDuplicate", {"cases_in": len(duplicate_cases)})

    results: List[Dict[str, Any]] = []
    for case in duplicate_cases:
        subject  = case.get("subject", "No subject")
        print(f"\n  [Slack] Duplicate notification for: {subject[:60]}")

        channel_id = await find_channel_id(search_tool, DEFAULT_CHANNEL_QUERY)
        case_out   = dict(case)

        if not channel_id:
            print(f"  [Slack] No channel found — skipping send")
            case_out["duplicate_slack_result"] = {
                "status":     "no_channel",
                "channel_id": "",
                "message_ts": "",
                "error":      f"No channel found for query='{DEFAULT_CHANNEL_QUERY}'",
            }
            results.append(case_out)
            continue

        message = build_duplicate_slack_message(case)
        print(f"  [Slack] Sending to channel {channel_id}...")

        try:
            result = await send_tool.acall(channel_id=channel_id, text=message)
            raw = str(result)
            print(f"  [Slack] Send raw: {raw[:300]}")

            text_match = re.search(r"text='(\{.*?\})'", raw, re.DOTALL)
            json_str   = text_match.group(1) if text_match else raw

            message_ts   = ""
            message_link = ""
            try:
                data = json.loads(json_str)
                if isinstance(data, dict):
                    message_ts   = data.get("ts", data.get("message_ts", ""))
                    message_link = data.get("message_link", data.get("link", ""))
            except (json.JSONDecodeError, TypeError):
                pass

            print(f"  [Slack] Duplicate notification sent: ts={message_ts}  link={message_link}")
            case_out["duplicate_slack_result"] = {
                "status":       "sent",
                "channel_id":   channel_id,
                "message_ts":   message_ts,
                "message_link": message_link,
            }

        except Exception as exc:
            print(f"  [Slack] ERROR sending duplicate notification: {type(exc).__name__}: {exc}")
            case_out["duplicate_slack_result"] = {
                "status":     "error",
                "channel_id": channel_id,
                "message_ts": "",
                "error":      str(exc),
            }

        results.append(case_out)
        sr_dup = case_out.get("duplicate_slack_result", {})
        log_agent_case(
            agent   = "NotifySlackDuplicate",
            inputs  = {"subject": case.get("subject", ""), "sender_email": case.get("sender_email", "")},
            outputs = {"status": sr_dup.get("status", ""), "channel_id": sr_dup.get("channel_id", "")},
        )

    log_agent_end("NotifySlackDuplicate", {"cases_notified": len(results)})

    return results


def build_validation_slack_message(case: Dict[str, Any]) -> str:
    subject  = case.get("subject", "No subject")
    sender   = case.get("sender_name", "Unknown")
    email    = case.get("sender_email", "")

    vr         = case.get("validation_result", {})
    label      = vr.get("label", "spam")
    reason     = vr.get("reason", "")
    confidence = vr.get("confidence", "medium")

    body_text = case.get("body_text", "").strip()

    if label == "spam":
        icon  = ":no_entry_sign:"
        title = "SPAM Email Detected — Skipped"
        note  = "This email was identified as spam or a marketing/promotional message. No ticket was created and no reply was sent."
    else:  # non_actionable
        icon  = ":information_source:"
        title = "Non-Actionable Email — No Action Required"
        note  = "This email does not describe a problem requiring support action (e.g. thank-you, compliment, positive feedback). No ticket was created and no reply was sent."

    return (
        f"{icon} *{title}*\n\n"
        f"*From:* {sender} <{email}>\n"
        f"*Subject:* {subject}\n"
        f"*Reason:* {reason}   *(confidence: {confidence})*\n\n"
        f"*Email content:*\n```{body_text}```\n\n"
        f"_{note}_"
    )



async def run_notify_slack_validation(
    rejected_cases: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    if not rejected_cases:
        return []

    labels = set(c.get("validation_result", {}).get("label", "?") for c in rejected_cases)
    print("\n" + "=" * 60)
    print("  NOTIFY SLACK AGENT — Validation-rejected notifications")
    print(f"  Cases to notify: {len(rejected_cases)}  labels={labels}")
    print("=" * 60)

    print("  [Slack] Connecting to local Slack MCP server...")
    search_tool, send_tool = await get_slack_tools()
    print("  [Slack] slack_search_channels + send_bot_message ready")

    log_agent_start("NotifySlackValidation", {"cases_in": len(rejected_cases)})

    results: List[Dict[str, Any]] = []
    for case in rejected_cases:
        subject = case.get("subject", "No subject")
        label   = case.get("validation_result", {}).get("label", "spam")
        print(f"\n  [Slack] Validation notification for [{label}]: {subject[:60]}")

        channel_id = await find_channel_id(search_tool, DEFAULT_CHANNEL_QUERY)
        case_out   = dict(case)

        if not channel_id:
            print(f"  [Slack] No channel found — skipping send")
            case_out["validation_slack_result"] = {
                "status":     "no_channel",
                "channel_id": "",
                "message_ts": "",
                "error":      f"No channel found for query='{DEFAULT_CHANNEL_QUERY}'",
            }
            results.append(case_out)
            continue

        message = build_validation_slack_message(case)
        print(f"  [Slack] Sending to channel {channel_id}...")

        try:
            result = await send_tool.acall(channel_id=channel_id, text=message)
            raw = str(result)
            print(f"  [Slack] Send raw: {raw[:300]}")

            text_match = re.search(r"text='(\{.*?\})'", raw, re.DOTALL)
            json_str   = text_match.group(1) if text_match else raw

            message_ts   = ""
            message_link = ""
            try:
                data = json.loads(json_str)
                if isinstance(data, dict):
                    message_ts   = data.get("ts", data.get("message_ts", ""))
                    message_link = data.get("message_link", data.get("link", ""))
            except (json.JSONDecodeError, TypeError):
                pass

            print(f"  [Slack] Validation notification sent: ts={message_ts}  link={message_link}")
            case_out["validation_slack_result"] = {
                "status":       "sent",
                "channel_id":   channel_id,
                "message_ts":   message_ts,
                "message_link": message_link,
            }

        except Exception as exc:
            print(f"  [Slack] ERROR sending validation notification: {type(exc).__name__}: {exc}")
            case_out["validation_slack_result"] = {
                "status":     "error",
                "channel_id": channel_id,
                "message_ts": "",
                "error":      str(exc),
            }

        results.append(case_out)
        sr_val = case_out.get("validation_slack_result", {})
        log_agent_case(
            agent   = "NotifySlackValidation",
            inputs  = {"subject": case.get("subject", ""), "sender_email": case.get("sender_email", "")},
            outputs = {"status": sr_val.get("status", ""), "label": case.get("validation_result", {}).get("label", "")},
        )

    log_agent_end("NotifySlackValidation", {"cases_notified": len(results)})

    return results

