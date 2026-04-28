"""
cost_tracker.py — LLM Usage & Cost Tracking Utility
─────────────────────────────────────────────────────
Tracks token usage and estimated cost for every Gemini LLM call
made during a workflow run.

Usage
─────
1. In workflow_agent.py, create a CostTracker and set it in the
   context var before running agents:

       tracker = CostTracker()
       set_tracker(tracker)

2. In agents that call the LLM directly, replace:

       response = await _llm.acomplete(prompt)
       text = response.text.strip()

   with:

       from utils.cost_tracker import tracked_complete
       text = await tracked_complete(_llm, prompt, "AgentName")

3. After the workflow finishes, copy results to WorkflowState:

       state.llm_usage = tracker.to_dict_list()
       state.total_cost_usd = tracker.total_cost_usd

Pricing (Gemini 2.5 Flash — as of mid-2025)
────────────────────────────────────────────
  Input  : $0.075 per 1M tokens
  Output : $0.30  per 1M tokens
"""
import time
import contextvars
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

# ── Gemini 2.5 Flash pricing ──────────────────────────────────
_INPUT_COST_PER_M  = 0.075   # USD per 1 million input tokens
_OUTPUT_COST_PER_M = 0.30    # USD per 1 million output tokens


# ── Per-call usage record ─────────────────────────────────────
@dataclass
class UsageEntry:
    agent: str
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    cost_usd: float
    duration_ms: float
    model: str = "gemini-2.5-flash"
    gmail_id: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "agent":             self.agent,
            "model":             self.model,
            "prompt_tokens":     self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "total_tokens":      self.total_tokens,
            "cost_usd":          round(self.cost_usd, 8),
            "duration_ms":       round(self.duration_ms, 1),
            "gmail_id":          self.gmail_id,
        }


# ── Tracker accumulates entries for a single workflow run ─────
class CostTracker:
    def __init__(self) -> None:
        self.entries: List[UsageEntry] = []

    def log(
        self,
        agent: str,
        prompt_tokens: int,
        completion_tokens: int,
        duration_ms: float,
        model: str = "gemini-2.5-flash",
        gmail_id: str = "",
    ) -> None:
        total = prompt_tokens + completion_tokens
        cost  = (
            (prompt_tokens     / 1_000_000) * _INPUT_COST_PER_M
            + (completion_tokens / 1_000_000) * _OUTPUT_COST_PER_M
        )
        entry = UsageEntry(
            agent=agent,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=total,
            cost_usd=cost,
            duration_ms=duration_ms,
            model=model,
            gmail_id=gmail_id,
        )
        self.entries.append(entry)
        print(
            f"[CostTracker] {agent} — "
            f"in={prompt_tokens} out={completion_tokens} "
            f"cost=${cost:.6f} ({duration_ms:.0f}ms)"
        )

    @property
    def total_cost_usd(self) -> float:
        return sum(e.cost_usd for e in self.entries)

    @property
    def total_tokens(self) -> int:
        return sum(e.total_tokens for e in self.entries)

    def to_dict_list(self) -> List[Dict[str, Any]]:
        return [e.to_dict() for e in self.entries]


# ── ContextVar so the tracker is available without threading issues ──
_tracker_var: contextvars.ContextVar[Optional[CostTracker]] = contextvars.ContextVar(
    "cost_tracker", default=None
)


def get_tracker() -> Optional[CostTracker]:
    """Return the CostTracker bound to the current async context, or None."""
    return _tracker_var.get()


def set_tracker(tracker: CostTracker) -> None:
    """Bind a CostTracker to the current async context."""
    _tracker_var.set(tracker)


# ── Drop-in replacement for llm.acomplete() ──────────────────
async def tracked_complete(llm: Any, prompt: str, agent_name: str, gmail_id: str = "") -> str:
    """
    Call llm.acomplete(prompt), record token usage + cost, return text.

    Falls back gracefully if usage_metadata is unavailable (e.g. in tests).
    """
    t0       = time.perf_counter()
    response = await llm.acomplete(prompt)
    elapsed  = (time.perf_counter() - t0) * 1000  # → ms

    tracker = get_tracker()
    if tracker is not None:
        # Gemini returns usage in response.raw["usage_metadata"]
        usage = {}
        if hasattr(response, "raw") and isinstance(response.raw, dict):
            usage = response.raw.get("usage_metadata", {})
        elif hasattr(response, "raw") and hasattr(response.raw, "__dict__"):
            usage = vars(response.raw).get("usage_metadata", {})

        # Handle both dict-style and attribute-style UsageMetadata objects
        def _get(obj, *keys):
            for k in keys:
                if isinstance(obj, dict):
                    val = obj.get(k)
                else:
                    val = getattr(obj, k, None)
                if val is not None:
                    return val
            return 0

        prompt_tokens     = _get(usage, "prompt_token_count",     "input_tokens")
        completion_tokens = _get(usage, "candidates_token_count", "output_tokens")

        tracker.log(
            agent=agent_name,
            prompt_tokens=int(prompt_tokens),
            completion_tokens=int(completion_tokens),
            duration_ms=elapsed,
            gmail_id=gmail_id,
        )

    return response.text.strip()
