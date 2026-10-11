"""Application-level Cerebras spend guard.

Hard constraint: roughly USD 4.00 remains on the account. This ledger enforces
a CONSERVATIVE application-level budget (default 4.00 total, 1.00/day, 0.05
per call) so development never intentionally burns the demo reserve.

This is NOT an account-wide cap — sibling apps, the dashboard playground, or
direct API use also draw from the same balance. The provider dashboard is
authoritative for the real balance; we only track locally-observed estimated
spend and stop OURSELVES when our guard trips.

Pricing source: https://inference-docs.cerebras.ai/support/pricing (captured
2026-07-01; verify before changing):
* gpt-oss-120b: $0.35 / $0.75 per M input/output tokens
* gemma-4-31b: $0.99 / $1.49 per M
* zai-glm-4.7 (preview, deprecating 2026-08-17): $2.25 / $2.75 per M
Unknown models default to the HIGHEST known rates (conservative).
"""

from __future__ import annotations

import json
import threading
from datetime import date
from pathlib import Path
from typing import Any

# (input $/M, output $/M)
CEREBRAS_PRICING: dict[str, tuple[float, float]] = {
    "gpt-oss-120b": (0.35, 0.75),
    "qwen-3-235b": (0.50, 1.00),
    "qwen-3-32b": (0.40, 0.80),
    "qwen-3.8-27b": (0.40, 0.80),
    "llama-3.3-70b": (0.85, 1.20),
    "llama-3.1-8b": (0.10, 0.20),
    "gemma-4-31b": (0.99, 1.49),
    "zai-glm-4.7": (2.25, 2.75),
}
PRICING_SOURCE = "https://inference-docs.cerebras.ai/support/pricing (captured 2026-07-01)"
_FALLBACK_RATES = (0.99, 1.49)  # conservative: highest common rates


def rates_for(model: str) -> tuple[float, float]:
    return CEREBRAS_PRICING.get((model or "").strip().lower(), _FALLBACK_RATES)


def estimate_cost_usd(model: str, input_tokens: int, output_tokens: int) -> float:
    inp, out = rates_for(model)
    return round(input_tokens / 1_000_000 * inp + output_tokens / 1_000_000 * out, 6)


def estimate_input_tokens(chars: int) -> int:
    """Rough heuristic (~4 chars/token) used ONLY for pre-call guards."""
    return max(1, int(chars / 4))


class CerebrasLedger:
    """Thread-safe local usage ledger with JSON persistence."""

    def __init__(self, path: Path | None = None) -> None:
        from app.config import settings as _s

        self._path = path or _s.cerebras_ledger_path
        self._lock = threading.Lock()
        self._state: dict[str, Any] = {
            "total_spend_usd": 0.0,
            "request_count": 0,
            "failed_count": 0,
            "total_input_tokens": 0,
            "total_output_tokens": 0,
            "daily": {},
            "disabled_reason": None,
            "events": [],
        }
        self._load()

    # -- persistence -----------------------------------------------------
    def _load(self) -> None:
        try:
            if self._path.exists():
                self._state.update(json.loads(self._path.read_text(encoding="utf-8")))
        except (OSError, ValueError):
            pass

    def _save(self) -> None:
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            self._path.write_text(json.dumps(self._state, indent=2), encoding="utf-8")
        except OSError:
            pass

    def reset(self) -> None:
        with self._lock:
            self._state = {
                "total_spend_usd": 0.0,
                "request_count": 0,
                "failed_count": 0,
                "total_input_tokens": 0,
                "total_output_tokens": 0,
                "daily": {},
                "disabled_reason": None,
                "events": [],
            }
            self._save()

    # -- guards ----------------------------------------------------------
    def _disabled(self) -> str | None:
        return self._state.get("disabled_reason")

    def check(self, estimated_cost: float) -> None:
        """Raise LLMBudgetExceededError if this call must not proceed."""
        from app.config import settings
        from app.llm.errors import LLMBudgetExceededError

        reason = self._disabled()
        if reason:
            raise LLMBudgetExceededError(f"Cerebras calls disabled: {reason}")
        total = float(self._state.get("total_spend_usd", 0.0))
        if total + estimated_cost > settings.CEREBRAS_BUDGET_USD:
            self._trip(f"Application budget USD {settings.CEREBRAS_BUDGET_USD:.2f} would be exceeded.")
            raise LLMBudgetExceededError(
                "Cerebras application budget would be exceeded; call blocked to "
                "preserve demo credits. Provider dashboard is authoritative."
            )
        if estimated_cost > settings.CEREBRAS_MAX_CALL_COST_USD:
            raise LLMBudgetExceededError(
                f"Estimated call cost USD {estimated_cost:.4f} exceeds per-call limit "
                f"USD {settings.CEREBRAS_MAX_CALL_COST_USD:.4f}; requires explicit approval."
            )
        today = date.today().isoformat()
        daily = float(self._state.get("daily", {}).get(today, 0.0))
        if daily + estimated_cost > settings.CEREBRAS_DAILY_BUDGET_USD:
            raise LLMBudgetExceededError(
                f"Daily application budget USD {settings.CEREBRAS_DAILY_BUDGET_USD:.2f} "
                "would be exceeded; call blocked."
            )

    def _trip(self, reason: str) -> None:
        self._state["disabled_reason"] = reason
        self._save()

    # -- recording -------------------------------------------------------
    def record(self, *, model: str, input_tokens: int, output_tokens: int, ok: bool = True) -> float:
        from app.config import settings

        cost = estimate_cost_usd(model, input_tokens, output_tokens)
        with self._lock:
            self._state["total_spend_usd"] = round(float(self._state.get("total_spend_usd", 0.0)) + cost, 6)
            self._state["total_input_tokens"] = int(self._state.get("total_input_tokens", 0)) + input_tokens
            self._state["total_output_tokens"] = (
                int(self._state.get("total_output_tokens", 0)) + output_tokens
            )
            if ok:
                self._state["request_count"] = int(self._state.get("request_count", 0)) + 1
            else:
                self._state["failed_count"] = int(self._state.get("failed_count", 0)) + 1
            today = date.today().isoformat()
            daily = dict(self._state.get("daily", {}))
            daily[today] = round(float(daily.get(today, 0.0)) + cost, 6)
            self._state["daily"] = daily
            if float(self._state["total_spend_usd"]) >= settings.CEREBRAS_BUDGET_USD:
                self._state["disabled_reason"] = (
                    f"Application budget USD {settings.CEREBRAS_BUDGET_USD:.2f} reached; "
                    "further Cerebras calls disabled to preserve demo credits."
                )
            self._save()
        return cost

    def record_failure(self) -> None:
        with self._lock:
            self._state["failed_count"] = int(self._state.get("failed_count", 0)) + 1
            self._save()

    # -- observability ---------------------------------------------------
    def snapshot(self) -> dict[str, Any]:
        from app.config import settings

        with self._lock:
            total = float(self._state.get("total_spend_usd", 0.0))
            today = date.today().isoformat()
            daily = float(dict(self._state.get("daily", {})).get(today, 0.0))
            return {
                "total_spend_usd": round(total, 6),
                "budget_usd": settings.CEREBRAS_BUDGET_USD,
                "remaining_application_budget_usd": round(max(0.0, settings.CEREBRAS_BUDGET_USD - total), 6),
                "daily_spend_usd": round(daily, 6),
                "daily_budget_usd": settings.CEREBRAS_DAILY_BUDGET_USD,
                "per_call_limit_usd": settings.CEREBRAS_MAX_CALL_COST_USD,
                "request_count": int(self._state.get("request_count", 0)),
                "failed_count": int(self._state.get("failed_count", 0)),
                "total_input_tokens": int(self._state.get("total_input_tokens", 0)),
                "total_output_tokens": int(self._state.get("total_output_tokens", 0)),
                "disabled": bool(self._state.get("disabled_reason")),
                "disabled_reason": self._state.get("disabled_reason"),
                "pricing_source": PRICING_SOURCE,
                "balance_note": (
                    "Locally-tracked ESTIMATED spend only — not the account balance. "
                    "The Cerebras provider dashboard is authoritative for the real balance."
                ),
            }


_ledger: CerebrasLedger | None = None
_ledger_lock = threading.Lock()


def get_ledger() -> CerebrasLedger:
    global _ledger
    with _ledger_lock:
        if _ledger is None:
            _ledger = CerebrasLedger()
        return _ledger


def reset_ledger(path: Path | None = None) -> CerebrasLedger:
    global _ledger
    with _ledger_lock:
        _ledger = CerebrasLedger(path=path) if path else CerebrasLedger()
        _ledger.reset()
        return _ledger


__all__ = [
    "CEREBRAS_PRICING",
    "PRICING_SOURCE",
    "CerebrasLedger",
    "estimate_cost_usd",
    "estimate_input_tokens",
    "get_ledger",
    "rates_for",
    "reset_ledger",
]
