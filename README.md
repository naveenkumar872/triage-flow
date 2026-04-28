# TriageFlow — AI-Powered Customer Email Triage Pipeline

TriageFlow is an end-to-end agentic AI system that automatically reads unread support emails from Gmail, classifies them, searches your Confluence knowledge base for solutions, creates Jira tickets for escalations, sends Slack notifications, and drafts reply emails — all without human intervention.

---

## Table of Contents

- [Demo](#demo)
- [Architecture Overview](#architecture-overview)
- [Pipeline Stages](#pipeline-stages)
- [Tech Stack](#tech-stack)
- [Project Structure](#project-structure)
- [Prerequisites](#prerequisites)
- [Environment Variables](#environment-variables)
- [Setup & Installation](#setup--installation)
- [Running the Application](#running-the-application)
- [API Endpoints](#api-endpoints)
- [Running Tests](#running-tests)
- [Security & Guardrails](#security--guardrails)
- [Observability](#observability)

---

## Demo

> 📹 Video walkthrough of the full pipeline — from raw Gmail inbox to Jira ticket, Slack notification, and auto-reply.

https://github.com/user-attachments/assets/2f793f45-febd-4649-83ff-bc920d7dfb34

---

## Architecture Overview

![Architecture Diagram](Architecture_Diagram.png)

```
Gmail Inbox
    │
    ▼
IntakeAgent          ← [direct] OAuth2 Gmail API — no LLM, no MCP
    │
    ▼
ValidationAgent      ← [direct LLM] Prompt injection detection + spam/valid filter
    │  (spam / non_actionable → Slack notify → EXIT)
    ▼
CustomerContextAgent ← [direct] TiDB MCP lookup (tier, company, open tickets)
    │                   [FunctionAgent] LLM + Atlassian MCP (searchJiraIssuesUsingJql)
    │                                   → semantic duplicate detection
    │  (duplicate → auto reply → EXIT)
    ▼
ConfluenceSearchAgent← [FunctionAgent] LLM + Atlassian MCP (searchConfluenceUsingCql)
    │                                   → decides if confident auto-reply is possible
    │  (confident answer found → auto reply → EXIT)
    ▼
TriageClassificationAgent ← [direct LLM] category, priority (P1–P4), sentiment, team
    │                         no MCP — pure structured LLM call + output normaliser
    ▼
JiraAgent            ← [direct MCP] Atlassian MCP: createJiraIssue — no LLM
    │
    ▼
NotifySlackAgent     ← [direct MCP] Custom FastMCP: send_bot_message — no LLM
    │
    ▼
ReplyAgent           ← [direct LLM] draft reply + Gmail API send — no MCP
```

---

## Pipeline Stages

| Step | Agent | Description |
|------|-------|-------------|
| 1 | **IntakeAgent** | Fetches up to 10 unread emails from Gmail via OAuth2. Marks processed emails with a `PROCESSED_BY_TRIAGE` label. |
| 2 | **ValidationAgent** | Detects prompt injection attacks (pre-LLM, zero API cost). Classifies emails as `spam`, `non_actionable`, or `valid_issue` using Gemini. |
| 3 | **CustomerContextAgent** | Looks up sender in TiDB Cloud (tier, company, open tickets). Detects duplicate issues using a **LLM-powered `FunctionAgent`** (`DuplicateCheckAgent`) that autonomously searches Jira via `searchJiraIssuesUsingJql` and semantically decides whether the new email matches any existing open ticket. |
| 4 | **ConfluenceSearchAgent** | Uses Atlassian MCP to run CQL searches against Confluence. LLM (via LlamaIndex `FunctionAgent`) decides if a confident auto-reply is possible. |
| 5 | **TriageClassificationAgent** | Classifies escalated cases: category, priority (P1–P4), sentiment, owning team, summary, tags. |
| 6 | **JiraAgent** | Creates a Jira issue via Atlassian MCP with triage metadata. |
| 7 | **NotifySlackAgent** | Posts a structured message to the support Slack channel via custom FastMCP server. |
| 8 | **ReplyAgent** | Drafts and sends a reply email via Gmail API. |

---

## Tech Stack

| Layer | Technology |
|-------|-----------|
| **LLM** | Google Gemini 2.5 Flash |
| **Agent Framework** | LlamaIndex (`FunctionAgent`, `BasicMCPClient`, `McpToolSpec`) |
| **Backend API** | FastAPI + Uvicorn (port 8080) |
| **Frontend** | React 18 + Vite + TailwindCSS |
| **Database** | TiDB Cloud (MySQL-compatible, customer profiles + routing rules) |
| **Email** | Gmail API (OAuth2) |
| **Knowledge Base** | Atlassian Confluence (via Atlassian Cloud MCP) |
| **Ticket Tracking** | Atlassian Jira (via Atlassian Cloud MCP) |
| **Notifications** | Slack (via custom FastMCP server + Slack SDK) |
| **Custom MCP Server** | FastMCP (port 9000) — Slack + TiDB tools |
| **Observability** | OpenLIT (OpenTelemetry LLM tracing + Prometheus metrics) |
| **Testing** | pytest |

---

## Project Structure

```
ragworks-final/
├── backend/
│   ├── main.py                        # FastAPI app — all HTTP endpoints
│   ├── agents/
│   │   ├── intake_agent.py            # Gmail OAuth2 email fetcher
│   │   ├── validation_agent.py        # Spam filter + prompt injection guardrail
│   │   ├── customer_context_agent.py  # TiDB customer lookup + LLM-powered duplicate detection 
│   │   ├── confluence_search_agent.py # Atlassian MCP CQL search + LLM analysis
│   │   ├── triage_classification_agent.py  # Priority / category / team classifier
│   │   ├── jira_agent.py              # Jira issue creation via MCP
│   │   ├── notify_slack_agent.py      # Slack notifications via MCP
│   │   ├── reply_agent.py             # Reply email drafter + Gmail sender
│   │   └── prompts.py                 # All LLM system/user prompts (centralised)
│   ├── workflow/
│   │   ├── workflow_agent.py          # Main pipeline orchestrator
│   │   └── workflow_state.py          # Shared state dataclass across agents
│   ├── mcp_tools/
│   │   └── server.py                  # Custom FastMCP server (Slack + TiDB tools)
│   ├── utils/
│   │   ├── cost_tracker.py            # Per-agent LLM token usage + USD cost tracking
│   │   └── agent_logger.py            # Structured per-agent run logging
│   ├── config/
│   │   ├── .env                       # All secrets (not committed)
│   │   ├── credentials.json           # Gmail OAuth2 client credentials (not committed)
│   │   └── token.json                 # Gmail OAuth2 token — auto-generated on first run
│   ├── data/                          # Agent output JSON files (runtime artifacts)
│   └── tests/
│       ├── conftest.py                # pytest path setup
│       ├── test_guardrails.py         # Unit tests — injection detection + sanitization
│       └── test_triage_normalizer.py  # Unit tests — priority/category/sentiment/team maps
└── frontend/
    ├── index.html
    ├── vite.config.js
    └── src/
        ├── App.jsx                    # Root — theme toggle, view switching
        ├── components/
        │   ├── EmailTable.jsx         # Inbox table with status badges
        │   ├── MonitorView.jsx        # Analytics dashboard — shows per-agent LLM usage (tokens, cost, model calls)
        │   ├── PipelineJourneyModal.jsx # Per-email pipeline step trace
        │   ├── EmailPreviewModal.jsx  # Email body preview
        │   ├── Sidebar.jsx            # Navigation
        │   └── StatsCard.jsx          # KPI cards
        └── data/
            └── mockEmails.js          # Development mock data
```

---

## Prerequisites

- Python 3.10+
- Node.js 18+
- A Google Cloud project with the **Gmail API** enabled
- An Atlassian Cloud account with **Confluence** and **Jira** access
- A **TiDB Cloud** cluster (free tier works)
- A **Slack** workspace with a bot token
- A **Google Gemini API** key

---

## Environment Variables

Create `backend/config/.env` with the following keys:

```env
ATLASSIAN_API_KEY=your_atlassian_api_token
ATLASSIAN_CONFLUENCE_API_KEY=your_atlassian_api_token
ATLASSIAN_CLOUD_ID=your_atlassian_cloud_id

TIDB_CONNECTION_STRING=mysql://user:password@host:port/dbname

JIRA_PROJECT_KEY=your_jira_project_key

GEMINI_API_KEY=your_gemini_api_key

SLACK_BOT_TOKEN=xoxb-your-slack-bot-token
```

> **Note:** Never commit `.env`, `credentials.json`, or `token.json` to version control. They are listed in `.gitignore`.

---

## Setup & Installation

### 1. Clone the repository

```bash
git clone https://github.com/your-username/triageflow.git
cd triageflow
```

### 2. Create and activate a Python virtual environment

```bash
python -m venv .venv

# Windows
.venv\Scripts\activate

# macOS / Linux
source .venv/bin/activate
```

### 3. Install Python dependencies

```bash
pip install -r requirements.txt
```

### 4. Configure environment variables

Copy the template and fill in your credentials:

```bash
cp backend/config/.env.example backend/config/.env
# Edit backend/config/.env with your actual keys
```

### 5. Set up Gmail OAuth2

1. Go to [Google Cloud Console](https://console.cloud.google.com/) → APIs & Services → Credentials
2. Create an **OAuth 2.0 Client ID** (Desktop application type)
3. Download the JSON and save it as `backend/config/credentials.json`
4. On first run, a browser window will open to complete the OAuth flow. The token is saved automatically to `backend/config/token.json`.

### 6. Install frontend dependencies

```bash
cd frontend
npm install
```

---

## Running the Application

### Start the custom MCP server (Terminal 1)

The MCP server provides Slack and TiDB tools to the pipeline agents.

```bash
cd backend
python mcp_tools/server.py
```

The server starts on `http://127.0.0.1:9000`.

### Start the FastAPI backend (Terminal 2)

```bash
cd backend
uvicorn main:app --reload --host 0.0.0.0 --port 8080
```

API docs are available at `http://localhost:8080/docs`.

### Start the React frontend (Terminal 3)

```bash
cd frontend
npm run dev
```

The dashboard opens at `http://localhost:5173`.

---

## API Endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/health` | Liveness check |
| `GET` | `/emails` | List unread Gmail inbox (no side-effects) |
| `POST` | `/emails/{gmail_id}/process` | Run full pipeline on one email by Gmail ID |
| `POST` | `/emails/process-selected` | Run pipeline on a list of selected Gmail IDs |
| `POST` | `/trigger` | Pull all unread Gmail emails and run the full pipeline |
| `POST` | `/process-email` | Run pipeline on a submitted email case dict (for testing) |

Interactive API docs: `http://localhost:8080/docs`

---

## Running Tests

Tests are located in `backend/tests/` and require no API keys or network access — all LLM calls are bypassed via the prompt-injection early-exit path or by testing pure normalisation logic.

```bash
cd backend
pytest tests/ -v
```

Expected output:

```
tests/test_guardrails.py::test_injection_ignore_previous_instructions PASSED
tests/test_guardrails.py::test_clean_password_reset_email PASSED
tests/test_guardrails.py::test_sanitize_body_caps_at_3000_chars PASSED
tests/test_guardrails.py::test_injection_case_blocked_as_spam_sync PASSED
...
tests/test_triage_normalizer.py::test_priority_critical_maps_to_p1 PASSED
tests/test_triage_normalizer.py::test_category_unknown_clamps_to_general PASSED
...
55 passed in ~3s
```

### What the tests cover

| File | Tests | Coverage |
|------|-------|----------|
| `test_guardrails.py` | 21 | `detect_prompt_injection()` — 8 attack patterns + 4 clean emails; `sanitize_body()` — length cap + redaction; `validate_single_case()` — injection path blocked as spam without any LLM call |
| `test_triage_normalizer.py` | 34 | `PRIORITY_MAP` — all 11 canonical mappings + unknown fallback; `CATEGORY_VALID`, `SENTIMENT_VALID`, `TEAM_VALID` — membership checks + unknown clamping via `@pytest.mark.parametrize` |

---

## Security & Guardrails

### Input Guardrails (ValidationAgent)

- **Prompt injection detection** — runs _before_ any LLM call (zero API cost). A compiled regex (`INJECTION_RE`) scans the email subject and body for known attack patterns:
  - `ignore all previous instructions`
  - `disregard your system prompt`
  - `you are now ...` / `act as ...`
  - LLaMA-style `[INST]` markers
  - Template injection `{{ }}`, token delimiters `<|...|>`
- Flagged emails are immediately classified as `spam` with `injection_detected: true` and never reach the LLM.

- **Body sanitization** — after detection, the body is hard-capped at 3000 characters and all remaining injection patterns are redacted to `[REDACTED]` before being sent to the LLM.

- **Sanitized body propagation** — the sanitized body is stored back in the case dict and flows to every downstream agent, so no agent ever receives the raw attacker-controlled text.

### Output Guardrails (TriageClassificationAgent)

- LLM outputs are clamped to strict whitelists before use:
  - `PRIORITY_MAP` — free-text priority → `P1`–`P4`
  - `CATEGORY_VALID` — unknown category → `"general"`
  - `SENTIMENT_VALID` — unknown sentiment → `"neutral"`
  - `TEAM_VALID` — unknown team → `"support"`

---

## Observability

OpenLIT is initialised on startup and instruments all Gemini LLM calls automatically:

```python
openlit.init(disable_batch=True)
```

- **LLM traces** — every agent's prompt + completion is captured as an OpenTelemetry span
- **Token + cost tracking** — `CostTracker` records input/output tokens and estimated USD cost per agent per run, returned in the workflow state as `llm_usage` and `total_cost_usd`
- **Prometheus metrics** — available at `http://localhost:8080/metrics`
- **Agent run logs** — appended to `backend/data/agent_run_log.txt` after every pipeline run
