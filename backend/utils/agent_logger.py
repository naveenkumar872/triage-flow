"""
agent_logger.py — Per-agent input/output logging utility
─────────────────────────────────────────────────────────
Writes a plain-text append log to data/agent_run_log.txt.
Every agent in the pipeline calls these helpers so the full
flow of each run — inputs, outputs, errors — is visible in
one file without any external dependencies or OpenLIT.

Public API
──────────
  log_run_start(run_id, case_count)           ← start of a workflow run
  log_run_end(run_id, status, step_log)       ← end of a workflow run
  log_agent_start(agent, inputs_dict)         ← beginning of an agent
  log_agent_end(agent, outputs_dict)          ← end of an agent (summary)
  log_agent_case(agent, inputs, outputs)      ← per-email result inside an agent
  log_agent_error(agent, error_str)           ← exception caught in an agent

All functions are safe to call from sync or async code.
"""

import os
import datetime

# ─── Log file path ────────────────────────────────────────────
_DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data")
LOG_FILE  = os.path.join(_DATA_DIR, "agent_run_log.txt")

# ─── Width constants ──────────────────────────────────────────
_WIDE  = "=" * 72
_THIN  = "-" * 72


# ─────────────────────────────────────────────────────────────
# Internal helper
# ─────────────────────────────────────────────────────────────
def _ts() -> str:
    return datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _write(block: str) -> None:
    """Append a text block to the log file, creating it if needed."""
    try:
        os.makedirs(_DATA_DIR, exist_ok=True)
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(block)
    except Exception as exc:
        print(f"  [AgentLogger] WARNING — could not write log: {exc}")


def _fmt_dict(d: dict, indent: int = 2) -> str:
    """Format a flat dict as indented key=value lines."""
    pad = " " * indent
    lines = []
    for k, v in d.items():
        v_str = str(v)
        # Truncate long values so the log stays readable
      
        lines.append(f"{pad}{k:<22}: {v_str}")
    return "\n".join(lines)


# ─────────────────────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────────────────────

def log_run_start(run_id: str, case_count: int) -> None:
    """Call once at the very beginning of run_workflow()."""
    _write(
        f"\n{_WIDE}\n"
        f"  WORKFLOW RUN START\n"
        f"  run_id     : {run_id}\n"
        f"  timestamp  : {_ts()}\n"
        f"  cases_in   : {case_count}\n"
        f"{_WIDE}\n"
    )


def log_run_end(run_id: str, status: str, step_log: list) -> None:
    """Call once at the very end of run_workflow()."""
    steps_txt = "\n".join(
        f"    [{s.get('agent','?'):30s}] {s.get('status','?'):12s}  {s.get('note','')}"
        for s in (step_log or [])
    )
    _write(
        f"\n{_WIDE}\n"
        f"  WORKFLOW RUN END\n"
        f"  run_id     : {run_id}\n"
        f"  timestamp  : {_ts()}\n"
        f"  status     : {status}\n"
        f"  steps      :\n{steps_txt}\n"
        f"{_WIDE}\n"
    )


def log_agent_start(agent: str, inputs: dict) -> None:
    """Call at the top of each agent's entry-point function."""
    _write(
        f"\n{_THIN}\n"
        f"[{_ts()}]  AGENT START: {agent}\n"
        f"{_fmt_dict(inputs)}\n"
        f"{_THIN}\n"
    )


def log_agent_end(agent: str, outputs: dict) -> None:
    """Call just before returning from each agent's entry-point function."""
    _write(
        f"[{_ts()}]  AGENT END: {agent}\n"
        f"{_fmt_dict(outputs)}\n"
        f"{_THIN}\n"
    )


def log_agent_case(agent: str, inputs: dict, outputs: dict) -> None:
    """Call once per email case inside an agent's processing loop."""
    _write(
        f"  [{_ts()}]  {agent} — CASE\n"
        f"  INPUT:\n{_fmt_dict(inputs, indent=4)}\n"
        f"  OUTPUT:\n{_fmt_dict(outputs, indent=4)}\n"
    )


def log_agent_error(agent: str, error: str) -> None:
    """Call whenever an exception is caught inside an agent."""
    _write(
        f"[{_ts()}]  AGENT ERROR: {agent}\n"
        f"  {error}\n"
        f"{_THIN}\n"
    )
