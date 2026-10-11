"""Hosted-AI provider package (Gemini + Cerebras, deterministic default).

Canonical usage — always go through the router, never a provider directly::

    from app.llm.router import get_router
    response = get_router().generate_for_task_sync(
        "obligation_extraction", prompt, response_model=ObligationList
    )
    # response.execution_mode in {"gemini","cerebras","deterministic",
    #                             "deterministic_fallback","cache"}
"""

from __future__ import annotations

from typing import Any

from app.llm.base import (
    LLMProvider,
    LLMResponse,
    LLMUsage,
    UnavailableProvider,
    check_citations,
    truncate_input,
)
from app.llm.cache import TTLCache, cache_key, get_cache, reset_cache
from app.llm.cerebras_provider import CerebrasProvider
from app.llm.deterministic import DeterministicProvider
from app.llm.embeddings import (
    EmbeddingProvider,
    NullEmbeddingProvider,
    TfidfEmbeddingProvider,
    cosine,
    embedding_status,
    get_embedder,
    tokenize,
)
from app.llm.errors import (
    LLMAuthenticationError,
    LLMBudgetExceededError,
    LLMConfigurationError,
    LLMError,
    LLMInvalidOutputError,
    LLMModelNotFoundError,
    LLMQuotaExhaustedError,
    LLMTransientError,
    LLMUnavailableError,
)
from app.llm.ledger import (
    CEREBRAS_PRICING,
    CerebrasLedger,
    estimate_cost_usd,
    estimate_input_tokens,
    get_ledger,
    rates_for,
    reset_ledger,
)
from app.llm.prompts import (
    MAPPING_PROMPT,
    OBLIGATION_EXTRACTION_PROMPT,
    OBLIGATION_SCHEMA,
    SYSTEM_PREAMBLE,
    build_mapping_prompt,
    build_obligation_prompt,
)
from app.llm.redaction import redact, redact_mapping
from app.llm.router import (
    DEFAULT_TASK_ROUTING,
    DETERMINISTIC_TASKS,
    LLMRouter,
    get_router,
    reset_router,
    resolve_routing,
)
from app.llm.schemas import (
    InterpretationResult,
    MappingItem,
    MappingList,
    ObligationItem,
    ObligationList,
    RemediationItem,
    RemediationList,
)


def get_llm() -> LLMProvider:
    """Deprecated compat shim: prefer get_router()."""
    router = get_router()
    name = router.provider_for_task("obligation_extraction")
    if name == "cerebras" and router.cerebras.available():
        return router.cerebras
    if name == "gemini" and router.gemini.available():
        return router.gemini
    if router.gemini.available():
        return router.gemini
    if router.cerebras.available():
        return router.cerebras
    return UnavailableProvider()


def llm_status() -> dict[str, Any]:
    """Deprecated compat shim: prefer get_router().provider_status_sync()."""
    return get_router().provider_status_sync()


def reset_providers() -> None:
    reset_router()
    reset_cache()


__all__ = [
    "LLMProvider",
    "LLMResponse",
    "LLMUsage",
    "UnavailableProvider",
    "check_citations",
    "truncate_input",
    "DeterministicProvider",
    "CerebrasProvider",
    "LLMRouter",
    "get_router",
    "reset_router",
    "resolve_routing",
    "DEFAULT_TASK_ROUTING",
    "DETERMINISTIC_TASKS",
    "CerebrasLedger",
    "get_ledger",
    "reset_ledger",
    "estimate_cost_usd",
    "estimate_input_tokens",
    "rates_for",
    "CEREBRAS_PRICING",
    "TTLCache",
    "get_cache",
    "reset_cache",
    "cache_key",
    "redact",
    "redact_mapping",
    "LLMError",
    "LLMConfigurationError",
    "LLMTransientError",
    "LLMQuotaExhaustedError",
    "LLMAuthenticationError",
    "LLMModelNotFoundError",
    "LLMInvalidOutputError",
    "LLMBudgetExceededError",
    "LLMUnavailableError",
    "ObligationItem",
    "ObligationList",
    "MappingItem",
    "MappingList",
    "RemediationItem",
    "RemediationList",
    "InterpretationResult",
    "EmbeddingProvider",
    "TfidfEmbeddingProvider",
    "NullEmbeddingProvider",
    "get_embedder",
    "embedding_status",
    "cosine",
    "tokenize",
    "get_llm",
    "llm_status",
    "reset_providers",
    "SYSTEM_PREAMBLE",
    "OBLIGATION_SCHEMA",
    "OBLIGATION_EXTRACTION_PROMPT",
    "MAPPING_PROMPT",
    "build_obligation_prompt",
    "build_mapping_prompt",
]
