"""Cerebras provider via the official cerebras-cloud-sdk (OpenAI-compatible chat API).

Credit protection is enforced BEFORE every call:
* pre-call cost estimate must fit CEREBRAS_MAX_CALL_COST_USD,
  CEREBRAS_DAILY_BUDGET_USD and CEREBRAS_BUDGET_USD;
* usage + cost are recorded in the ledger AFTER every completed request;
* when the application budget trips, further calls are disabled with a clear
  message (no infinite retries, no parallel unbounded bursts — the router
  serialises calls with a semaphore).
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

from pydantic import BaseModel, ValidationError

from app.config import settings
from app.llm.base import LLMProvider, LLMResponse, LLMUsage, check_citations, truncate_input
from app.llm.errors import (
    LLMAuthenticationError,
    LLMBudgetExceededError,
    LLMConfigurationError,
    LLMInvalidOutputError,
    LLMModelNotFoundError,
    LLMQuotaExhaustedError,
    LLMTransientError,
)
from app.llm.ledger import estimate_cost_usd, estimate_input_tokens, get_ledger
from app.llm.redaction import redact
from app.logging_config import get_logger

log = get_logger("regulens.llm.cerebras")

_API_BASE = "https://api.cerebras.ai/v1"


def _classify(exc: Exception) -> Exception:
    msg = redact(str(exc))
    code: int | None = getattr(exc, "status_code", None) or getattr(exc, "status", None)
    name = type(exc).__name__
    blob = f"{name} {code} {msg}".lower()
    if isinstance(exc, LLMBudgetExceededError):
        return exc
    if code in (401, 403) or "unauthorized" in blob or "invalid api key" in blob or "authentication" in blob:
        return LLMAuthenticationError(f"Cerebras authentication failed: {msg[:300]}")
    if code == 404 or ("model" in blob and "not found" in blob):
        return LLMModelNotFoundError(f"Cerebras model unavailable: {msg[:300]}")
    if code == 429 or "429" in blob or "rate limit" in blob or "quota" in blob or "too many requests" in blob:
        return LLMQuotaExhaustedError(f"Cerebras rate limit/quota: {msg[:300]}")
    if code in (408, 500, 502, 503, 504) or "timeout" in blob or "timed out" in blob or "unavailable" in blob:
        return LLMTransientError(f"Cerebras transient failure: {msg[:300]}")
    if "timeout" in name.lower() or "connect" in name.lower():
        return LLMTransientError(f"Cerebras transient failure: {msg[:300]}")
    return LLMTransientError(f"Cerebras request failed: {msg[:300]}")


class CerebrasProvider(LLMProvider):
    name = "cerebras"

    def __init__(self, api_key: str | None = None, model: str | None = None) -> None:
        self._api_key = api_key if api_key is not None else settings.CEREBRAS_API_KEY
        self.model = (model or settings.CEREBRAS_MODEL or "").strip()

    # -- config ----------------------------------------------------------
    def available(self) -> bool:
        if settings.DETERMINISTIC_MODE:
            return False
        if not settings.CEREBRAS_ENABLED:
            return False
        if not (self._api_key or "").strip():
            return False
        if not self.model:
            return False
        try:
            if get_ledger().snapshot().get("disabled"):
                return False
        except Exception:  # noqa: BLE001
            pass
        return True

    def _precheck(self, prompt_chars: int, max_output: int) -> float:
        """Estimate + enforce budget BEFORE any network call. Returns estimate."""
        est_in = estimate_input_tokens(prompt_chars)
        est = estimate_cost_usd(self.model, est_in, max_output)
        get_ledger().check(est)
        return est

    # -- health (free models.list only — never burns completion credits) --
    async def health_check(self) -> dict[str, Any]:
        if not settings.CEREBRAS_ENABLED:
            return {"provider": self.name, "available": False, "reason": "CEREBRAS_ENABLED is false"}
        if settings.DETERMINISTIC_MODE:
            return {
                "provider": self.name,
                "available": False,
                "reason": "DETERMINISTIC_MODE forces local-only operation",
            }
        if not (self._api_key or "").strip():
            return {"provider": self.name, "available": False, "reason": "CEREBRAS_API_KEY is missing"}
        if not self.model:
            return {"provider": self.name, "available": False, "reason": "CEREBRAS_MODEL is empty"}
        ledger = get_ledger().snapshot()
        if ledger.get("disabled"):
            return {
                "provider": self.name,
                "available": False,
                "model": self.model,
                "reason": ledger.get("disabled_reason"),
                "ledger": ledger,
            }
        try:
            models = await asyncio.wait_for(asyncio.to_thread(self._list_models_sync), timeout=15)
        except LLMAuthenticationError as exc:
            return {
                "provider": self.name,
                "available": False,
                "model": self.model,
                "reason": redact(str(exc))[:200],
                "ledger": ledger,
            }
        except Exception as exc:  # noqa: BLE001
            return {
                "provider": self.name,
                "available": False,
                "model": self.model,
                "reason": redact(str(exc))[:200],
                "ledger": ledger,
            }
        if self.model not in models:
            return {
                "provider": self.name,
                "available": False,
                "model": self.model,
                "reason": f"Model '{self.model}' not in account model list ({len(models)} models). "
                "Verify against current official docs; do not assume availability.",
                "known_models": sorted(models)[:12],
                "ledger": ledger,
            }
        return {
            "provider": self.name,
            "available": True,
            "model": self.model,
            "note": "Model verified; spend guards active.",
            "ledger": ledger,
        }

    def _list_models_sync(self) -> list[str]:
        import httpx

        try:
            resp = httpx.get(
                f"{_API_BASE}/models",
                headers={"Authorization": f"Bearer {(self._api_key or '').strip()}"},
                timeout=10,
            )
        except Exception as exc:  # noqa: BLE001
            raise _classify(exc) from exc
        if resp.status_code in (401, 403):
            raise LLMAuthenticationError("Cerebras key rejected by /v1/models.")
        if resp.status_code == 429:
            raise LLMQuotaExhaustedError("Cerebras rate limit on /v1/models.")
        if resp.status_code >= 500:
            raise LLMTransientError(f"Cerebras models endpoint HTTP {resp.status_code}.")
        try:
            data = resp.json()
        except ValueError as exc:
            raise LLMTransientError(f"Cerebras models endpoint returned non-JSON: {exc}") from exc
        items = data.get("data", data if isinstance(data, list) else [])
        return [str(i.get("id", i)) for i in items if isinstance(i, (dict, str))]

    # -- generation ------------------------------------------------------
    async def generate_text(
        self, prompt: str, *, system: str | None = None, max_output_tokens: int | None = None
    ) -> LLMResponse:
        if not (self._api_key or "").strip() or not settings.CEREBRAS_ENABLED:
            raise LLMConfigurationError("Cerebras is not enabled/configured.")
        bounded = min(
            max_output_tokens or settings.CEREBRAS_MAX_OUTPUT_TOKENS, settings.CEREBRAS_MAX_OUTPUT_TOKENS
        )
        prompt, truncated = truncate_input(prompt, settings.LLM_MAX_INPUT_CHARS)
        warnings = ["Input truncated to LLM_MAX_INPUT_CHARS."] if truncated else []
        self._precheck(len(prompt) + len(system or ""), bounded)
        started = time.perf_counter()
        last: Exception | None = None
        for attempt in range(settings.CEREBRAS_MAX_RETRIES + 1):
            try:
                text, usage = await asyncio.wait_for(
                    asyncio.to_thread(self._chat_sync, prompt, system, bounded, False, None),
                    timeout=settings.CEREBRAS_TIMEOUT_SECONDS,
                )
                cost = get_ledger().record(
                    model=self.model, input_tokens=usage.input_tokens, output_tokens=usage.output_tokens
                )
                return LLMResponse(
                    text=text,
                    model=self.model,
                    provider=self.name,
                    latency_ms=int((time.perf_counter() - started) * 1000),
                    warnings=warnings,
                    usage=usage,
                    cost_usd=cost,
                    execution_mode="cerebras",
                )
            except LLMBudgetExceededError:
                raise
            except (LLMQuotaExhaustedError, LLMTransientError) as exc:
                last = exc
                get_ledger().record_failure()
                log.warning("cerebras.attempt_failed", attempt=attempt, error=redact(str(exc))[:200])
                if attempt < settings.CEREBRAS_MAX_RETRIES:
                    await asyncio.sleep(0.5 * (2**attempt))
                    continue
                raise
            except (
                LLMAuthenticationError,
                LLMModelNotFoundError,
                LLMConfigurationError,
                LLMInvalidOutputError,
            ):
                get_ledger().record_failure()
                raise
            except Exception as exc:  # noqa: BLE001 - classify raw SDK/transport errors, retry if transient
                mapped = _classify(exc)
                get_ledger().record_failure()
                if isinstance(mapped, (LLMQuotaExhaustedError, LLMTransientError)):
                    last = mapped
                    log.warning("cerebras.attempt_failed", attempt=attempt, error=redact(str(mapped))[:200])
                    if attempt < settings.CEREBRAS_MAX_RETRIES:
                        await asyncio.sleep(0.5 * (2**attempt))
                        continue
                raise mapped from exc
        raise last or LLMTransientError("Cerebras request failed without details.")

    async def generate_structured(
        self,
        prompt: str,
        *,
        system: str | None = None,
        response_model: type[BaseModel],
        max_output_tokens: int | None = None,
    ) -> LLMResponse:
        if not (self._api_key or "").strip() or not settings.CEREBRAS_ENABLED:
            raise LLMConfigurationError("Cerebras is not enabled/configured.")
        bounded = min(
            max_output_tokens or settings.CEREBRAS_MAX_OUTPUT_TOKENS, settings.CEREBRAS_MAX_OUTPUT_TOKENS
        )
        prompt, truncated = truncate_input(prompt, settings.LLM_MAX_INPUT_CHARS)
        warnings = ["Input truncated to LLM_MAX_INPUT_CHARS."] if truncated else []
        # Cerebras json_object mode requires the word JSON in the prompt.
        if "json" not in prompt.lower():
            prompt = prompt + "\n\nReturn the answer as JSON matching the requested schema."
        self._precheck(len(prompt) + len(system or ""), bounded)
        started = time.perf_counter()
        last: Exception | None = None
        for attempt in range(settings.CEREBRAS_MAX_RETRIES + 1):
            try:
                text, usage = await asyncio.wait_for(
                    asyncio.to_thread(self._chat_sync, prompt, system, bounded, True, response_model),
                    timeout=settings.CEREBRAS_TIMEOUT_SECONDS,
                )
                try:
                    parsed = response_model.model_validate_json(text)
                except (ValueError, ValidationError) as exc:
                    raise LLMInvalidOutputError(
                        f"Cerebras returned malformed output: {redact(str(exc))[:300]}"
                    ) from exc
                warnings = warnings + check_citations(parsed, prompt)
                cost = get_ledger().record(
                    model=self.model, input_tokens=usage.input_tokens, output_tokens=usage.output_tokens
                )
                return LLMResponse(
                    text=text,
                    model=self.model,
                    provider=self.name,
                    latency_ms=int((time.perf_counter() - started) * 1000),
                    warnings=warnings,
                    parsed_json=parsed,
                    usage=usage,
                    cost_usd=cost,
                    execution_mode="cerebras",
                )
            except LLMInvalidOutputError:
                get_ledger().record_failure()
                raise
            except LLMBudgetExceededError:
                raise
            except (LLMQuotaExhaustedError, LLMTransientError) as exc:
                last = exc
                get_ledger().record_failure()
                log.warning("cerebras.attempt_failed", attempt=attempt, error=redact(str(exc))[:200])
                if attempt < settings.CEREBRAS_MAX_RETRIES:
                    await asyncio.sleep(0.5 * (2**attempt))
                    continue
                raise
            except (LLMAuthenticationError, LLMModelNotFoundError, LLMConfigurationError):
                get_ledger().record_failure()
                raise
            except Exception as exc:  # noqa: BLE001 - classify raw SDK/transport errors, retry if transient
                mapped = _classify(exc)
                get_ledger().record_failure()
                if isinstance(mapped, (LLMQuotaExhaustedError, LLMTransientError)):
                    last = mapped
                    log.warning("cerebras.attempt_failed", attempt=attempt, error=redact(str(mapped))[:200])
                    if attempt < settings.CEREBRAS_MAX_RETRIES:
                        await asyncio.sleep(0.5 * (2**attempt))
                        continue
                raise mapped from exc
        raise last or LLMTransientError("Cerebras request failed without details.")

    # -- sync SDK call ----------------------------------------------------
    def _chat_sync(
        self,
        prompt: str,
        system: str | None,
        max_tokens: int,
        want_json: bool,
        response_model: type[BaseModel] | None,
    ) -> tuple[str, LLMUsage]:
        try:
            from cerebras.cloud.sdk import Cerebras
        except ImportError as exc:
            raise LLMConfigurationError(
                "cerebras-cloud-sdk is not installed (pip install cerebras-cloud-sdk)."
            ) from exc
        messages = [
            {
                "role": "system",
                "content": system or "You are a concise compliance assistant. Answer with JSON when asked.",
            },
            {"role": "user", "content": prompt},
        ]
        kwargs: dict[str, Any] = {"model": self.model, "messages": messages, "temperature": 0.0}
        if want_json:
            kwargs["response_format"] = {"type": "json_object"}
        try:
            client = Cerebras(api_key=(self._api_key or "").strip())
            try:
                resp = client.chat.completions.create(max_completion_tokens=max_tokens, **kwargs)
            except TypeError:
                resp = client.chat.completions.create(max_tokens=max_tokens, **kwargs)
        except Exception as exc:  # noqa: BLE001
            raise _classify(exc) from exc
        try:
            text = (resp.choices[0].message.content or "") if resp.choices else ""
        except (AttributeError, IndexError) as exc:
            raise LLMInvalidOutputError(
                f"Cerebras returned an unsupported payload: {redact(str(exc))[:200]}"
            ) from exc
        if want_json and not text.strip():
            raise LLMInvalidOutputError("Cerebras returned an empty response for a structured request.")
        usage = LLMUsage()
        raw = getattr(resp, "usage", None)
        if raw is not None:
            try:
                usage = LLMUsage(
                    input_tokens=int(getattr(raw, "prompt_tokens", 0) or 0),
                    output_tokens=int(getattr(raw, "completion_tokens", 0) or 0),
                )
            except (TypeError, ValueError):
                usage = LLMUsage()
        if usage.total_tokens == 0:
            usage = LLMUsage(
                input_tokens=estimate_input_tokens(len(prompt) + len(system or "")),
                output_tokens=estimate_input_tokens(len(text)),
            )
        return text, usage


__all__ = ["CerebrasProvider"]
