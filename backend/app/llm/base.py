"""Provider-independent LLM interface (Gemini + Cerebras + deterministic).

Application modules must NOT import a concrete provider. They call::

    from app.llm.router import get_router
    result = get_router().generate_for_task_sync("obligation_extraction", prompt, response_model=...)

Routing, fallback, caching, budgets and usage tracking live in
:mod:`app.llm.router`. Providers only translate one bounded request.

Execution modes reported to API/UI: ``gemini`` | ``cerebras`` |
``deterministic`` | ``deterministic_fallback`` | ``cache``.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel

from app.llm.errors import LLMConfigurationError


@dataclass
class LLMUsage:
    input_tokens: int = 0
    output_tokens: int = 0

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens


@dataclass
class LLMResponse:
    text: str
    model: str
    provider: str
    deterministic_fallback: bool = False
    latency_ms: int = 0
    warnings: list[str] = field(default_factory=list)
    parsed_json: Any = None
    # --- observability (new) ---
    usage: LLMUsage = field(default_factory=LLMUsage)
    cost_usd: float = 0.0
    cached: bool = False
    execution_mode: str = "deterministic"

    @property
    def total_tokens(self) -> int:
        return self.usage.total_tokens


def truncate_input(text: str, max_chars: int) -> tuple[str, bool]:
    """Bound prompt size. Returns (possibly truncated text, was_truncated)."""
    if len(text) <= max_chars:
        return text, False
    return text[:max_chars], True


class LLMProvider(ABC):
    """Provider-independent interface every backend implements."""

    name: str = "abstract"

    @abstractmethod
    def available(self) -> bool:
        """Cheap config check (no network, no credits)."""

    @abstractmethod
    async def generate_text(
        self,
        prompt: str,
        *,
        system: str | None = None,
        max_output_tokens: int | None = None,
    ) -> LLMResponse: ...

    @abstractmethod
    async def generate_structured(
        self,
        prompt: str,
        *,
        system: str | None = None,
        response_model: type[BaseModel],
        max_output_tokens: int | None = None,
    ) -> LLMResponse: ...

    @abstractmethod
    async def health_check(self) -> dict[str, Any]: ...

    def status(self) -> dict[str, Any]:
        try:
            return {"provider": self.name, "available": self.available()}
        except Exception as exc:  # noqa: BLE001
            return {"provider": self.name, "available": False, "reason": str(exc)[:200]}


def check_citations(parsed: BaseModel, source_text: str, *, limit: int = 5) -> list[str]:
    """Warn when a model quotes text that is not verbatim in the source.

    This never fabricates citations; it only flags possible fabrication for
    human review. Callers keep the model output but must surface warnings.
    """
    warnings: list[str] = []
    candidates: list[str] = []
    data = parsed.model_dump()
    _collect_quotes(data, candidates)
    for quote in candidates[:limit]:
        q = (quote or "").strip()
        if q and q not in source_text:
            warnings.append(
                "Model quoted_text is not verbatim in the supplied source; "
                "treat as interpretation requiring human review."
            )
            break
    return warnings


def _collect_quotes(node: Any, out: list[str]) -> None:
    if isinstance(node, dict):
        for key, value in node.items():
            if key in ("quoted_text", "citations") and isinstance(value, str):
                out.append(value)
            elif key == "citations" and isinstance(value, list):
                out.extend([v for v in value if isinstance(v, str)])
            else:
                _collect_quotes(value, out)
    elif isinstance(node, list):
        for item in node:
            _collect_quotes(item, out)


# ---------------------------------------------------------------------------
# Backwards compatibility: the old sync UnavailableProvider.
# New code should use DeterministicProvider (async). Kept so existing callers
# and tests fail loudly instead of silently inventing answers.
# ---------------------------------------------------------------------------
class UnavailableProvider(LLMProvider):
    """Deterministic placeholder: raises rather than inventing an answer."""

    name = "unavailable"

    def available(self) -> bool:
        return False

    def generate(
        self, prompt: str, *, system: str | None = None, json_schema: dict | None = None
    ) -> LLMResponse:
        raise RuntimeError(
            "No hosted AI provider is configured. The system runs in deterministic "
            "mode — no model output is claimed. Configure GEMINI_API_KEY and/or "
            "CEREBRAS_API_KEY to enable AI-assisted tasks."
        )

    async def generate_text(
        self, prompt: str, *, system: str | None = None, max_output_tokens: int | None = None
    ) -> LLMResponse:
        raise LLMConfigurationError(
            "No hosted AI provider is configured (deterministic mode). "
            "This task requires language-model reasoning: returning review-required."
        )

    async def generate_structured(
        self,
        prompt: str,
        *,
        system: str | None = None,
        response_model: type[BaseModel],
        max_output_tokens: int | None = None,
    ) -> LLMResponse:
        raise LLMConfigurationError(
            "No hosted AI provider is configured (deterministic mode). "
            "This task requires language-model reasoning: returning review-required."
        )

    async def health_check(self) -> dict[str, Any]:
        return {
            "provider": self.name,
            "available": False,
            "reason": "deterministic mode: no hosted provider enabled or configured",
        }

    def status(self) -> dict[str, Any]:
        return {
            "provider": self.name,
            "available": False,
            "reason": "deterministic mode: hosted AI disabled or not configured",
        }


__all__ = [
    "LLMProvider",
    "LLMResponse",
    "LLMUsage",
    "UnavailableProvider",
    "check_citations",
    "truncate_input",
]
