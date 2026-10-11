"""Deterministic provider — always available, never calls a hosted model.

Used for every task that deterministic Python rules perform reliably
(extraction parsers, diffs, evidence tests, risk rules, OR-Tools optimisation,
citation checks, deadline enforcement) and as the final fallback when hosted
providers are unavailable. For tasks that genuinely need LM reasoning its
generate_* methods raise :class:`LLMUnavailableError` so callers return an
explicit unavailable / review-required status instead of fabricated output.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel

from app.llm.base import LLMProvider, LLMResponse
from app.llm.errors import LLMUnavailableError


class DeterministicProvider(LLMProvider):
    name = "deterministic"

    def available(self) -> bool:
        return True

    async def generate_text(
        self, prompt: str, *, system: str | None = None, max_output_tokens: int | None = None
    ) -> LLMResponse:
        raise LLMUnavailableError(
            "Deterministic mode: this task requires language-model reasoning, "
            "which is unavailable. Marked as review-required; no output fabricated."
        )

    async def generate_structured(
        self,
        prompt: str,
        *,
        system: str | None = None,
        response_model: type[BaseModel],
        max_output_tokens: int | None = None,
    ) -> LLMResponse:
        raise LLMUnavailableError(
            "Deterministic mode: this task requires language-model reasoning, "
            "which is unavailable. Marked as review-required; no output fabricated."
        )

    async def health_check(self) -> dict[str, Any]:
        return {
            "provider": self.name,
            "available": True,
            "mode": "deterministic",
            "note": "Rule-based pipelines only; AI-assisted tasks return review-required.",
        }


__all__ = ["DeterministicProvider"]
