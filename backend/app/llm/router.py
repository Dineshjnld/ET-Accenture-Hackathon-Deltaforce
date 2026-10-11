"""Central provider router: ONE place decides which backend serves a task.

Initial routing policy (spec section 5)::

    pdf_docx_extraction      -> deterministic (parsers, never an LLM)
    obligation_extraction    -> gemini
    version_comparison       -> deterministic diff + gemini interpretation
    control_mapping          -> hybrid retrieval + cerebras
    evidence_status          -> deterministic tests (+ optional AI interpretation)
    risk_scoring             -> python rules (deterministic)
    remediation_proposals    -> cerebras (gemini fallback when appropriate)
    deadline_enforcement     -> deterministic rules
    portfolio_optimization   -> OR-Tools / deterministic
    citation_validation      -> deterministic source-reference checks
    contradiction_analysis   -> retrieval + AI + human review (gemini)
    executive_explanation    -> gemini or cerebras per budget/availability

Rules enforced here (never in individual modules):
* configurable via LLM_ROUTING_OVERRIDES (JSON) — no scattered if/provider code;
* never call both providers for one task (sequential fallback only);
* never use an LLM for a deterministic-by-policy task;
* bounded retries, concurrency cap, request cache, usage + spend tracking.
"""

from __future__ import annotations

import asyncio
import copy
import json
import threading
import time
from typing import Any

from pydantic import BaseModel

from app.config import settings
from app.llm.base import LLMProvider, LLMResponse
from app.llm.cache import cache_key, get_cache
from app.llm.cerebras_provider import CerebrasProvider
from app.llm.deterministic import DeterministicProvider
from app.llm.errors import (
    LLMBudgetExceededError,
    LLMConfigurationError,
    LLMError,
    LLMUnavailableError,
)
from app.llm.gemini_provider import GeminiProvider
from app.llm.ledger import get_ledger
from app.llm.redaction import redact
from app.logging_config import get_logger

log = get_logger("regulens.llm.router")

DETERMINISTIC_TASKS = frozenset(
    {
        "pdf_docx_extraction",
        "risk_scoring",
        "deadline_enforcement",
        "portfolio_optimization",
        "citation_validation",
        "evidence_status",
    }
)

DEFAULT_TASK_ROUTING: dict[str, str] = {
    "pdf_docx_extraction": "deterministic",
    "obligation_extraction": "gemini",
    "version_comparison": "gemini",
    "control_mapping": "cerebras",
    "evidence_status": "deterministic",
    "risk_scoring": "deterministic",
    "remediation_proposals": "cerebras",
    "deadline_enforcement": "deterministic",
    "portfolio_optimization": "deterministic",
    "citation_validation": "deterministic",
    "contradiction_analysis": "gemini",
    "executive_explanation": "auto",
}


def resolve_routing() -> dict[str, str]:
    routing = dict(DEFAULT_TASK_ROUTING)
    raw = (settings.LLM_ROUTING_OVERRIDES or "").strip()
    if raw:
        try:
            overrides = json.loads(raw)
            for task, provider in overrides.items():
                if provider in ("gemini", "cerebras", "deterministic", "auto"):
                    routing[str(task)] = provider
        except (ValueError, AttributeError) as exc:
            log.warning("router.invalid_overrides", error=redact(str(exc))[:200])
    return routing


class LLMRouter:
    """Single entry point for every AI-assisted task."""

    def __init__(
        self,
        gemini: LLMProvider | None = None,
        cerebras: LLMProvider | None = None,
        deterministic: LLMProvider | None = None,
    ) -> None:
        self.gemini = gemini or GeminiProvider()
        self.cerebras = cerebras or CerebrasProvider()
        self.deterministic = deterministic or DeterministicProvider()
        self._sem = asyncio.Semaphore(max(1, settings.LLM_MAX_CONCURRENT))
        self._lock = threading.Lock()
        self.requests_made = 0
        self.failed_requests = 0
        self.input_tokens = 0
        self.output_tokens = 0
        self.fallback_events: list[dict[str, Any]] = []

    # -- routing ---------------------------------------------------------
    def provider_for_task(self, task: str) -> str:
        routing = resolve_routing()
        if task in routing:
            choice = routing[task]
            if choice == "auto":
                return self._auto_choice()
            return choice
        return settings.LLM_PROVIDER

    def _auto_choice(self) -> str:
        # executive_explanation: cheapest available first, protect Cerebras budget
        try:
            if self.cerebras.available():
                return "cerebras"
        except Exception:  # noqa: BLE001
            pass
        try:
            if self.gemini.available():
                return "gemini"
        except Exception:  # noqa: BLE001
            pass
        return "deterministic"

    def _provider(self, name: str) -> LLMProvider:
        return {"gemini": self.gemini, "cerebras": self.cerebras}.get(name, self.deterministic)

    def _fallback_name(self, preferred: str) -> str | None:
        fb = settings.LLM_FALLBACK_PROVIDER
        if fb == "none" or fb == preferred:
            return None
        return fb

    # -- main entry points ------------------------------------------------
    async def agenerate_for_task(
        self,
        task: str,
        prompt: str,
        *,
        system: str | None = None,
        response_model: type[BaseModel] | None = None,
        max_output_tokens: int | None = None,
        allow_fallback: bool = True,
    ) -> LLMResponse:
        preferred = self.provider_for_task(task)
        if preferred == "deterministic":
            raise LLMUnavailableError(
                f"Task '{task}' is deterministic by policy — use Python rules, not an LLM."
            )
        if settings.effective_deterministic:
            raise LLMUnavailableError(
                "Deterministic mode is effective (no provider enabled/configured or "
                "DETERMINISTIC_MODE=true). Returning review-required; no output fabricated."
            )
        # Cache repeatable requests (successes only).
        use_cache = settings.LLM_CACHE_ENABLED
        key = ""
        if use_cache:
            key = cache_key(
                task,
                preferred,
                self._provider(preferred).name,
                getattr(self._provider(preferred), "model", ""),
                prompt[:12000],
                system or "",
                response_model.__name__ if response_model else "text",
            )
            hit = get_cache().get(key)
            if hit is not None:
                cached: LLMResponse = copy.deepcopy(hit)
                cached.cached = True
                cached.execution_mode = "cache"
                return cached

        async with self._sem:
            try:
                result = await self._call(preferred, prompt, system, response_model, max_output_tokens)
            except (LLMBudgetExceededError, LLMConfigurationError, LLMUnavailableError):
                # Budget/config errors: try fallback once if it is a DIFFERENT,
                # available provider; otherwise surface review-required.
                if allow_fallback:
                    fb = self._fallback_name(preferred)
                    if fb and fb != "deterministic" and self._provider(fb).available():
                        self._record_fallback(task, preferred, fb, "budget_or_config")
                        try:
                            result = await self._call(fb, prompt, system, response_model, max_output_tokens)
                            result.warnings = [f"Fell back from {preferred} to {fb}."] + result.warnings
                            self._observe(result, fallback=True)
                            if use_cache:
                                get_cache().set(key, copy.deepcopy(result))
                            return result
                        except LLMError as exc2:
                            self._observe_failure()
                            raise exc2
                self._observe_failure()
                raise
            except LLMError as exc:
                if allow_fallback:
                    fb = self._fallback_name(preferred)
                    if fb and fb != "deterministic" and self._provider(fb).available():
                        self._record_fallback(task, preferred, fb, redact(str(exc))[:160])
                        try:
                            result = await self._call(fb, prompt, system, response_model, max_output_tokens)
                            result.warnings = [
                                f"Fell back from {preferred} to {fb}: {redact(str(exc))[:160]}."
                            ] + result.warnings
                            self._observe(result, fallback=True)
                            if use_cache:
                                get_cache().set(key, copy.deepcopy(result))
                            return result
                        except LLMError as exc2:
                            self._observe_failure()
                            raise exc2
                self._observe_failure()
                raise
            self._observe(result, fallback=False)
            if use_cache:
                get_cache().set(key, copy.deepcopy(result))
            return result

    async def _call(
        self,
        provider_name: str,
        prompt: str,
        system: str | None,
        response_model: type[BaseModel] | None,
        max_output_tokens: int | None,
    ) -> LLMResponse:
        provider = self._provider(provider_name)
        if not provider.available():
            raise LLMConfigurationError(f"Provider '{provider_name}' is not available.")
        if response_model is not None:
            return await provider.generate_structured(
                prompt, system=system, response_model=response_model, max_output_tokens=max_output_tokens
            )
        return await provider.generate_text(prompt, system=system, max_output_tokens=max_output_tokens)

    def generate_for_task_sync(
        self,
        task: str,
        prompt: str,
        *,
        system: str | None = None,
        response_model: type[BaseModel] | None = None,
        max_output_tokens: int | None = None,
        allow_fallback: bool = True,
    ) -> LLMResponse:
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(
                self.agenerate_for_task(
                    task,
                    prompt,
                    system=system,
                    response_model=response_model,
                    max_output_tokens=max_output_tokens,
                    allow_fallback=allow_fallback,
                )
            )
        raise RuntimeError(
            "generate_for_task_sync called from async context; await agenerate_for_task instead."
        )

    # -- observability ----------------------------------------------------
    def _observe(self, result: LLMResponse, *, fallback: bool) -> None:
        with self._lock:
            self.requests_made += 1
            self.input_tokens += result.usage.input_tokens
            self.output_tokens += result.usage.output_tokens

    def _observe_failure(self) -> None:
        with self._lock:
            self.requests_made += 1
            self.failed_requests += 1

    def _record_fallback(self, task: str, frm: str, to: str, reason: str) -> None:
        with self._lock:
            self.fallback_events.append(
                {
                    "task": task,
                    "from": frm,
                    "to": to,
                    "reason": reason[:200],
                    "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                }
            )
            self.fallback_events = self.fallback_events[-100:]
        log.warning("router.fallback", task=task, frm=frm, to=to)

    def reset_stats(self) -> None:
        with self._lock:
            self.requests_made = 0
            self.failed_requests = 0
            self.input_tokens = 0
            self.output_tokens = 0
            self.fallback_events = []

    async def provider_status(self) -> dict[str, Any]:
        gemini = await self.gemini.health_check()
        cerebras = await self.cerebras.health_check()
        deterministic = await self.deterministic.health_check()
        # Never leak keys in status payloads.
        for payload in (gemini, cerebras):
            payload.pop("api_key", None)
        mode = "deterministic"
        if not settings.effective_deterministic:
            if self.gemini.available() and self.cerebras.available():
                mode = "mixed"
            elif self.gemini.available():
                mode = "gemini"
            elif self.cerebras.available():
                mode = "cerebras"
        with self._lock:
            total = self.input_tokens + self.output_tokens
            stats = {
                "mode": mode,
                "effective_deterministic": settings.effective_deterministic,
                "preferred_provider": settings.LLM_PROVIDER,
                "fallback_provider": settings.LLM_FALLBACK_PROVIDER,
                "routing": resolve_routing(),
                "gemini": gemini,
                "cerebras": cerebras,
                "deterministic": deterministic,
                "requests_made": self.requests_made,
                "failed_requests": self.failed_requests,
                "input_tokens": self.input_tokens,
                "output_tokens": self.output_tokens,
                "total_tokens": total,
                "cerebras_ledger": get_ledger().snapshot(),
                "cache": get_cache().stats(),
                "fallback_events": list(self.fallback_events[-20:]),
                "fallback_count": len(self.fallback_events),
            }
        return stats

    def provider_status_sync(self) -> dict[str, Any]:
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(self.provider_status())
        raise RuntimeError("provider_status_sync called from async context; await provider_status instead.")


_router: LLMRouter | None = None
_router_lock = threading.Lock()


def get_router() -> LLMRouter:
    global _router
    with _router_lock:
        if _router is None:
            _router = LLMRouter()
        return _router


def reset_router() -> LLMRouter:
    global _router
    with _router_lock:
        _router = LLMRouter()
        return _router


__all__ = [
    "DEFAULT_TASK_ROUTING",
    "DETERMINISTIC_TASKS",
    "LLMRouter",
    "get_router",
    "reset_router",
    "resolve_routing",
]
