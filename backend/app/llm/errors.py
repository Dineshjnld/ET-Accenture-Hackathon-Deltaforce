"""LLM error hierarchy.

Retry policy:
* Transient (timeout, 429, 5xx, network blip) -> retry within strict per-provider
  limits, then fall back once to the alternative provider.
* Permanent (auth, disabled, budget exceeded, invalid output after retries,
  unavailable) -> do NOT retry the same provider; fall back once if allowed,
  otherwise surface deterministic / review-required status.

Nothing here ever fabricates model output.
"""

from __future__ import annotations


class LLMError(Exception):
    """Base class for all provider errors."""


class LLMConfigurationError(LLMError):
    """Missing key, disabled provider, or unconfigured model."""


class LLMTransientError(LLMError):
    """Retryable: timeout, HTTP 429, 5xx, network error."""


class LLMQuotaExhaustedError(LLMTransientError):
    """Rate limit / quota exhaustion. Retryable only within strict limits."""


class LLMAuthenticationError(LLMError):
    """401/403 — never retry with the same credentials."""


class LLMModelNotFoundError(LLMError):
    """Configured model id is not available to this account."""


class LLMInvalidOutputError(LLMError):
    """Malformed JSON or schema validation failure. The output is rejected."""


class LLMBudgetExceededError(LLMError):
    """Application-level Cerebras spend guard tripped. No API call is made."""


class LLMUnavailableError(LLMError):
    """No provider available and the task genuinely needs LM reasoning.

    Callers must convert this into an explicit ``unavailable`` /
    ``review-required`` status — never into fabricated findings.
    """


__all__ = [
    "LLMError",
    "LLMConfigurationError",
    "LLMTransientError",
    "LLMQuotaExhaustedError",
    "LLMAuthenticationError",
    "LLMModelNotFoundError",
    "LLMInvalidOutputError",
    "LLMBudgetExceededError",
    "LLMUnavailableError",
]
