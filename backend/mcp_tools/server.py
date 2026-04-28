"""
Custom Support Triage MCP Server
=================================
Connects to TiDB Cloud via TIDB_CONNECTION_STRING from config/.env.
Tools are added one by one as the pipeline agents are defined.

"""

import sys
import asyncio

# Suppress Windows ProactorEventLoop socket teardown noise (WinError 10054 / SSL errors)
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

import os
from datetime import datetime, timedelta

from dotenv import load_dotenv
from fastmcp import FastMCP
from slack_sdk import WebClient
from sqlalchemy import create_engine, text

# =========================================================
# ENV
# =========================================================
_CONFIG_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "config")
load_dotenv(os.path.join(_CONFIG_DIR, ".env"))

# =========================================================
# TIDB CONNECTION
# =========================================================

_raw_url = os.getenv("TIDB_CONNECTION_STRING")
if not _raw_url:
    raise RuntimeError("TIDB_CONNECTION_STRING is not set in config/.env")

# Ensure SQLAlchemy-compatible driver prefix
if _raw_url.startswith("mysql://"):
    _raw_url = _raw_url.replace("mysql://", "mysql+pymysql://", 1)

# Module-level engine — shared across all tools
engine = create_engine(
    _raw_url,
    connect_args={"ssl": {"ca": None}},  # TiDB Cloud requires TLS
    pool_pre_ping=True,                  # drop stale connections automatically
    future=True,
)

# Verify connection on startup
with engine.connect() as _conn:
    _conn.execute(text("SELECT 1"))
print("[server] TiDB connection OK")

# =========================================================
# SLACK CLIENT
# =========================================================
_SLACK_BOT_TOKEN = os.getenv("SLACK_BOT_TOKEN")
if not _SLACK_BOT_TOKEN:
    raise RuntimeError("SLACK_BOT_TOKEN is not set in config/.env")

slack_client = WebClient(token=_SLACK_BOT_TOKEN)


# =========================================================
# FASTMCP APP
# =========================================================

mcp = FastMCP(
    name="custom-support-triage",
)

# =========================================================
# TOOLS — added here one by one
# =========================================================

# ---------------------------------------------------------
# Tool 1 — get_customer_profile
# Input : email (str)
# Output: customer row from `customers` table
#         → id, name, tier, company, phone, is_active,
#           account_created_at, created_at
# ---------------------------------------------------------
@mcp.tool()
def get_customer_profile(email: str) -> dict:
    """
    Fetch customer profile from the customers table by email.

    Args:
        email: Customer email address (unique key).

    Returns:
        dict with found (bool) and profile fields:
        id, name, email, tier, company, phone,
        is_active, account_created_at, created_at.
    """
    with engine.connect() as conn:
        row = conn.execute(
            text("""
                SELECT
                    id, name, email, tier, company, phone,
                    is_active, account_created_at, created_at
                FROM customers
                WHERE email = :email
                LIMIT 1
            """),
            {"email": email.lower().strip()},
        ).fetchone()

    if not row:
        return {
            "found": False,
            "profile": {},
            "message": f"No customer found for email: {email}",
        }

    return {
        "found": True,
        "profile": {
            "id":                 row.id,
            "name":               row.name,
            "email":              row.email,
            "tier":               row.tier,
            "company":            row.company,
            "phone":              row.phone,
            "is_active":          bool(row.is_active),
            "account_created_at": str(row.account_created_at),
            "created_at":         str(row.created_at),
        },
        "message": "Customer profile retrieved successfully",
    }


# ---------------------------------------------------------
# Tool 2 — get_ticket_history
# Input : customer_id (int)
# Output: list of past tickets from `tickets` table
#         → id, subject, ticket_type, urgency, status,
#           assigned_team, created_at, resolved_at
# ---------------------------------------------------------
@mcp.tool()
def get_ticket_history(customer_id: int) -> dict:
    """
    Fetch all past tickets for a customer from the tickets table.

    Args:
        customer_id: Customer's internal ID (from get_customer_profile).

    Returns:
        dict with ticket_count and tickets list, each containing:
        id, subject, ticket_type, urgency, status, assigned_team,
        jira_id, created_at, resolved_at.
    """
    with engine.connect() as conn:
        rows = conn.execute(
            text("""
                SELECT
                    id, subject, ticket_type, urgency, status,
                    assigned_team, jira_id,
                    created_at, resolved_at
                FROM tickets
                WHERE customer_id = :customer_id
                ORDER BY created_at DESC
            """),
            {"customer_id": customer_id},
        ).fetchall()

    tickets = [
        {
            "id":            row.id,
            "subject":       row.subject,
            "ticket_type":   row.ticket_type,
            "urgency":       row.urgency,
            "status":        row.status,
            "assigned_team": row.assigned_team,
            "jira_id":       row.jira_id,
            "created_at":    str(row.created_at),
            "resolved_at":   str(row.resolved_at) if row.resolved_at else None,
        }
        for row in rows
    ]

    return {
        "customer_id":  customer_id,
        "ticket_count": len(tickets),
        "tickets":      tickets,
        "message":      f"{len(tickets)} ticket(s) found for customer {customer_id}",
    }


# ---------------------------------------------------------
# Tool 3 — send_bot_message
# Input : channel_id (str), text (str)
# Output: Slack send confirmation
# ---------------------------------------------------------
@mcp.tool()
def send_bot_message(channel_id: str, text: str) -> dict:
    """
    Send a Slack message as the bot.

    Args:
        channel_id: Slack channel ID (e.g. C1234567890).
        text: Message text to send.

    Returns:
        dict with ok, channel, ts, message.
    """
    response = slack_client.chat_postMessage(channel=channel_id, text=text)
    return {
        "ok":      response["ok"],
        "channel": response["channel"],
        "ts":      response["ts"],
        "message": text,
    }


# ---------------------------------------------------------
# Tool 4 — slack_search_channels
# Input : query (str), limit (int, default 20)
# Output: list of matching channels with id, name, topic,
#         purpose, is_archived, num_members
# ---------------------------------------------------------
@mcp.tool()
def slack_search_channels(query: str, limit: int = 20) -> dict:
    """
    Search for Slack channels by name or description. Returns channel names, IDs, topics, purposes, and archive status.

    Query tips: use terms matching channel names/descriptions (e.g., "engineering", "project alpha"). Names are typically lowercase with hyphens.

    Use slack_read_channel to read messages from a known channel. Use slack_search_public to search message content across channels.

    Args:
        query: Search term to match against channel name, topic, or purpose.
        limit: Maximum number of results to return (default 20).

    Returns:
        dict with channels list and count.
    """
    response = slack_client.conversations_list(
        types="public_channel,private_channel",
        limit=200,
        exclude_archived=False,
    )

    query_lower = query.lower()
    matches = []

    for channel in response.get("channels", []):
        name    = channel.get("name", "")
        topic   = channel.get("topic", {}).get("value", "")
        purpose = channel.get("purpose", {}).get("value", "")

        if (
            query_lower in name.lower()
            or query_lower in topic.lower()
            or query_lower in purpose.lower()
        ):
            matches.append({
                "id":          channel.get("id"),
                "name":        name,
                "topic":       topic,
                "purpose":     purpose,
                "is_archived": channel.get("is_archived", False),
                "num_members": channel.get("num_members", 0),
            })

        if len(matches) >= limit:
            break

    return {"channels": matches, "count": len(matches)}


# =========================================================
# ENTRY POINT
# =========================================================
if __name__ == "__main__":
    mcp.run(transport="streamable-http", host="0.0.0.0", port=9000)