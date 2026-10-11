"""Gemini + Cerebras provider tests (all mocked — zero API credits).

Covers: structured outputs, invalid JSON, missing keys, timeouts, 429/quota,
retry limits, fallback routing, budget enforcement, caching, secret
redaction, deterministic mode, usage tracking, and startup with neither
provider enabled.
"""

from __future__ import annotations

import pytest

from app.config import settings
from app.llm.base import LLMProvider, LLMResponse, LLMUsage
from app.llm.cache import reset_cache
from app.llm.errors import (
    LLMBudgetExceededError,
    LLMConfigurationError,
    LLMInvalidOutputError,
    LLMQuotaExhaustedError,
    LLMTransientError,
    LLMUnavailableError,
)
from app.llm.ledger import reset_ledger
from app.llm.redaction import redact
from app.llm.router import LLMRouter, reset_router
from app.llm.schemas import MappingList, ObligationList


@pytest.fixture(autouse=True)
def _isolated(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "DETERMINISTIC_MODE", False)
    monkeypatch.setattr(settings, "GEMINI_ENABLED", True)
    monkeypatch.setattr(settings, "GEMINI_API_KEY", "test-gemini-key-xyz")
    monkeypatch.setattr(settings, "GEMINI_MODEL", "gemini-2.0-flash")
    monkeypatch.setattr(settings, "GEMINI_MAX_RETRIES", 2)
    monkeypatch.setattr(settings, "GEMINI_TIMEOUT_SECONDS", 5)
    monkeypatch.setattr(settings, "GEMINI_MAX_OUTPUT_TOKENS", 512)
    monkeypatch.setattr(settings, "CEREBRAS_ENABLED", True)
    monkeypatch.setattr(settings, "CEREBRAS_API_KEY", "test-cerebras-key-xyz")
    monkeypatch.setattr(settings, "CEREBRAS_MODEL", "gpt-oss-120b")
    monkeypatch.setattr(settings, "CEREBRAS_MAX_RETRIES", 2)
    monkeypatch.setattr(settings, "CEREBRAS_TIMEOUT_SECONDS", 5)
    monkeypatch.setattr(settings, "CEREBRAS_MAX_OUTPUT_TOKENS", 256)
    monkeypatch.setattr(settings, "CEREBRAS_BUDGET_USD", 4.00)
    monkeypatch.setattr(settings, "CEREBRAS_MAX_CALL_COST_USD", 0.05)
    monkeypatch.setattr(settings, "CEREBRAS_DAILY_BUDGET_USD", 1.00)
    monkeypatch.setattr(settings, "LLM_PROVIDER", "gemini")
    monkeypatch.setattr(settings, "LLM_FALLBACK_PROVIDER", "cerebras")
    monkeypatch.setattr(settings, "LLM_ROUTING_OVERRIDES", "")
    monkeypatch.setattr(settings, "LLM_MAX_INPUT_CHARS", 24000)
    monkeypatch.setattr(settings, "LLM_CACHE_ENABLED", True)
    monkeypatch.setattr(settings, "CEREBRAS_LEDGER_PATH", str(tmp_path / "ledger.json"))
    reset_ledger()
    reset_cache()
    reset_router()
    yield


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------
class StubProvider(LLMProvider):
    name = "stub"

    def __init__(self, behaviour="ok", model="stub-model"):
        self.behaviour = behaviour
        self.model = model
        self.calls = 0

    def available(self) -> bool:
        if settings.DETERMINISTIC_MODE:
            return False
        return self.behaviour != "disabled"

    async def generate_text(self, prompt, *, system=None, max_output_tokens=None) -> LLMResponse:
        self.calls += 1
        return self._result(prompt, None)

    async def generate_structured(
        self, prompt, *, system=None, response_model, max_output_tokens=None
    ) -> LLMResponse:
        self.calls += 1
        return self._result(prompt, response_model)

    def _result(self, prompt, response_model):
        if self.behaviour == "transient":
            raise LLMTransientError("stub transient boom")
        if self.behaviour == "quota":
            raise LLMQuotaExhaustedError("stub 429 quota exhausted")
        if self.behaviour == "config":
            raise LLMConfigurationError("stub not configured")
        if self.behaviour == "ok":
            parsed = None
            text = '{"ok": true}'
            if response_model is not None:
                if response_model is ObligationList:
                    text = (
                        '{"obligations": [{"obligation_text": "The bank shall appoint an officer.", '
                        '"quoted_text": "The bank shall appoint an officer.", "severity": "high", '
                        '"confidence": 0.8, "warnings": []}]}'
                    )
                elif response_model is MappingList:
                    text = (
                        '{"mappings": [{"control_uid": "CTL-1", "rationale": "covers appointment", '
                        '"confidence": 0.7, "warnings": []}]}'
                    )
                else:
                    text = "{}"
                parsed = response_model.model_validate_json(text)
            return LLMResponse(
                text=text,
                model=self.model,
                provider=self.name,
                usage=LLMUsage(input_tokens=10, output_tokens=5),
                parsed_json=parsed,
                execution_mode=self.name,
            )
        raise AssertionError(f"unknown stub behaviour {self.behaviour}")

    async def health_check(self):
        return {"provider": self.name, "available": self.available()}


def _router_with(g_behaviour="ok", c_behaviour="ok"):
    g = StubProvider(g_behaviour, model="gemini-2.0-flash")
    g.name = "gemini"
    c = StubProvider(c_behaviour, model="gpt-oss-120b")
    c.name = "cerebras"
    return LLMRouter(gemini=g, cerebras=c), g, c


# ---------------------------------------------------------------------------
# Gemini (mocked at the sync SDK-call boundary)
# ---------------------------------------------------------------------------
async def test_gemini_structured_output_validated(monkeypatch):
    from app.llm.gemini_provider import GeminiProvider

    p = GeminiProvider(api_key="k", model="gemini-2.0-flash")
    quote = "The bank shall appoint an officer."
    import json as _json

    payload = _json.dumps(
        {
            "obligations": [
                {
                    "obligation_text": quote,
                    "quoted_text": quote,
                    "severity": "high",
                    "confidence": 0.8,
                    "warnings": [],
                }
            ]
        }
    )
    monkeypatch.setattr(
        p,
        "_generate_json_sync",
        lambda *a, **k: (payload, LLMUsage(input_tokens=100, output_tokens=50)),
    )
    prompt = f"<untrusted_document>{quote}</untrusted_document> extract"
    resp = await p.generate_structured(prompt, response_model=ObligationList)
    assert resp.execution_mode == "gemini"
    assert resp.parsed_json is not None
    assert resp.parsed_json.obligations[0].quoted_text == quote
    assert resp.usage.input_tokens == 100


async def test_gemini_invalid_json_rejected(monkeypatch):
    from app.llm.gemini_provider import GeminiProvider

    p = GeminiProvider(api_key="k", model="gemini-2.0-flash")
    monkeypatch.setattr(p, "_generate_json_sync", lambda *a, **k: ("this is not json", LLMUsage()))
    with pytest.raises(LLMInvalidOutputError):
        await p.generate_structured("prompt", response_model=ObligationList)


async def test_gemini_missing_key_unavailable(monkeypatch):
    from app.llm.gemini_provider import GeminiProvider

    monkeypatch.setattr(settings, "GEMINI_API_KEY", "")
    p = GeminiProvider(api_key="", model="gemini-2.0-flash")
    assert p.available() is False
    with pytest.raises(LLMConfigurationError):
        await p.generate_text("hi")
    health = await p.health_check()
    assert health["available"] is False
    assert "GEMINI_API_KEY" in health.get("reason", "")


async def test_gemini_timeout_retries_bounded(monkeypatch):
    from app.llm.gemini_provider import GeminiProvider

    p = GeminiProvider(api_key="k", model="gemini-2.0-flash")
    calls = {"n": 0}

    def _boom(*a, **k):
        calls["n"] += 1
        raise TimeoutError("timed out")

    monkeypatch.setattr(p, "_generate_text_sync", _boom)
    with pytest.raises(LLMTransientError):
        await p.generate_text("hi")
    assert calls["n"] == settings.GEMINI_MAX_RETRIES + 1


async def test_gemini_429_maps_to_quota(monkeypatch):
    from app.llm.gemini_provider import GeminiProvider, _classify_exception

    class Fake429(Exception):
        code = 429

    err = _classify_exception(Fake429("rate limit exceeded"))
    assert isinstance(err, LLMQuotaExhaustedError)

    p = GeminiProvider(api_key="k", model="gemini-2.0-flash")
    calls = {"n": 0}

    def _q(*a, **k):
        calls["n"] += 1
        raise err

    monkeypatch.setattr(p, "_generate_text_sync", _q)
    with pytest.raises(LLMQuotaExhaustedError):
        await p.generate_text("hi")
    assert calls["n"] == settings.GEMINI_MAX_RETRIES + 1


# ---------------------------------------------------------------------------
# Cerebras (mocked at the sync SDK-call boundary)
# ---------------------------------------------------------------------------
async def test_cerebras_structured_output_and_ledger(monkeypatch):
    from app.llm.cerebras_provider import CerebrasProvider
    from app.llm.ledger import get_ledger

    p = CerebrasProvider(api_key="k", model="gpt-oss-120b")
    monkeypatch.setattr(
        p,
        "_chat_sync",
        lambda *a, **k: (
            '{"mappings": [{"control_uid": "CTL-1", "rationale": "covers it", '
            '"confidence": 0.7, "warnings": []}]}',
            LLMUsage(input_tokens=200, output_tokens=100),
        ),
    )
    resp = await p.generate_structured("decide JSON for quiz", response_model=MappingList)
    assert resp.execution_mode == "cerebras"
    assert resp.parsed_json.mappings[0].control_uid == "CTL-1"
    snap = get_ledger().snapshot()
    assert snap["request_count"] == 1
    assert snap["total_input_tokens"] == 200
    assert snap["total_spend_usd"] > 0


async def test_cerebras_invalid_json_rejected(monkeypatch):
    from app.llm.cerebras_provider import CerebrasProvider

    p = CerebrasProvider(api_key="k", model="gpt-oss-120b")
    monkeypatch.setattr(p, "_chat_sync", lambda *a, **k: ("nope[[[", LLMUsage(10, 5)))
    with pytest.raises(LLMInvalidOutputError):
        await p.generate_structured("answer as JSON please", response_model=MappingList)


async def test_cerebras_missing_key_unavailable():
    from app.llm.cerebras_provider import CerebrasProvider

    p = CerebrasProvider(api_key="", model="gpt-oss-120b")
    assert p.available() is False
    with pytest.raises(LLMConfigurationError):
        await p.generate_text("hi")


async def test_cerebras_budget_blocks_before_any_call(monkeypatch):
    from app.llm.cerebras_provider import CerebrasProvider

    monkeypatch.setattr(settings, "CEREBRAS_MAX_CALL_COST_USD", 0.000001)
    p = CerebrasProvider(api_key="k", model="gpt-oss-120b")
    called = {"n": 0}

    def _spy(*a, **k):
        called["n"] += 1
        return "{}", LLMUsage()

    monkeypatch.setattr(p, "_chat_sync", _spy)
    with pytest.raises(LLMBudgetExceededError):
        await p.generate_text("x" * 5000)
    assert called["n"] == 0  # no network call was issued


async def test_cerebras_ledger_disables_after_budget(monkeypatch):
    from app.llm.cerebras_provider import CerebrasProvider
    from app.llm.ledger import get_ledger

    monkeypatch.setattr(settings, "CEREBRAS_BUDGET_USD", 0.0001)
    p = CerebrasProvider(api_key="k", model="gpt-oss-120b")
    monkeypatch.setattr(p, "_chat_sync", lambda *a, **k: ("ok", LLMUsage(10000, 10000)))
    with pytest.raises(LLMBudgetExceededError):
        await p.generate_text("hi")  # pre-check already exceeds the tiny budget
    snap = get_ledger().snapshot()
    assert snap["disabled"] is True
    assert p.available() is False


async def test_cerebras_429_and_retry_limit(monkeypatch):
    from app.llm.cerebras_provider import CerebrasProvider, _classify

    class Fake429(Exception):
        status_code = 429

    assert isinstance(_classify(Fake429("too many requests")), LLMQuotaExhaustedError)

    p = CerebrasProvider(api_key="k", model="gpt-oss-120b")
    calls = {"n": 0}

    def _q(*a, **k):
        calls["n"] += 1
        raise LLMQuotaExhaustedError("429 idea")

    monkeypatch.setattr(p, "_chat_sync", _q)
    with pytest.raises(LLMQuotaExhaustedError):
        await p.generate_text("hi")
    assert calls["n"] == settings.CEREBRAS_MAX_RETRIES + 1


# ---------------------------------------------------------------------------
# Router: fallback, caching, deterministic policy
# ---------------------------------------------------------------------------
async def test_router_fallback_records_event():
    router, g, c = _router_with("transient", "ok")
    resp = await router.agenerate_for_task(
        "obligation_extraction", "extract this", response_model=ObligationList
    )
    assert resp.provider == "cerebras"  # fell back from gemini
    assert g.calls == 1  # preferred tried exactly once (provider owns retries)
    assert c.calls == 1  # fallback tried exactly once — never both in parallel
    assert len(router.fallback_events) == 1
    assert router.fallback_events[0]["from"] == "gemini"
    assert router.fallback_events[0]["to"] == "cerebras"


async def test_router_never_calls_both_on_success():
    router, g, c = _router_with("ok", "ok")
    resp = await router.agenerate_for_task(
        "obligation_extraction", "extract this", response_model=ObligationList
    )
    assert resp.provider == "gemini"
    assert g.calls == 1
    assert c.calls == 0  # fallback must NOT fire on success


async def test_router_deterministic_task_refused():
    router, g, c = _router_with("ok", "ok")
    with pytest.raises(LLMUnavailableError):
        await router.agenerate_for_task("risk_scoring", "score this")
    assert g.calls == 0 and c.calls == 0


async def test_router_cache_hit_avoids_second_call():
    router, g, c = _router_with("ok", "ok")
    first = await router.agenerate_for_task(
        "obligation_extraction", "same prompt", response_model=ObligationList
    )
    second = await router.agenerate_for_task(
        "obligation_extraction", "same prompt", response_model=ObligationList
    )
    assert first.cached is False
    assert second.cached is True
    assert second.execution_mode == "cache"
    assert g.calls == 1


async def test_router_usage_tracking():
    router, g, c = _router_with("ok", "ok")
    await router.agenerate_for_task("obligation_extraction", "p1", response_model=ObligationList)
    await router.agenerate_for_task("control_mapping", "p2", response_model=MappingList)
    status = await router.provider_status()
    assert status["requests_made"] == 2
    assert status["failed_requests"] == 0
    assert status["total_tokens"] == 2 * (10 + 5)
    assert "cerebras_ledger" in status and "cache" in status


# ---------------------------------------------------------------------------
# Redaction, deterministic mode, startup
# ---------------------------------------------------------------------------
def test_secret_redaction():
    dirty = "key=AIzaSyD-SECRETVALUE123 auth Bearer csk-live-abcdef123456 GEMINI_API_KEY=supersecretvalue"
    clean = redact(dirty)
    assert "AIzaSyD-SECRETVALUE123" not in clean
    assert "csk-live-abcdef123456" not in clean
    assert "supersecretvalue" not in clean
    assert "[REDACTED]" in clean


async def test_deterministic_mode_blocks_hosted(monkeypatch):
    monkeypatch.setattr(settings, "DETERMINISTIC_MODE", True)
    router, g, c = _router_with("ok", "ok")
    assert router.gemini.available() is False
    with pytest.raises(LLMUnavailableError):
        await router.agenerate_for_task("obligation_extraction", "hi", response_model=ObligationList)
    assert g.calls == 0 and c.calls == 0


async def test_provider_status_never_contains_keys(monkeypatch):
    router = LLMRouter(gemini=StubProvider("disabled"), cerebras=StubProvider("disabled"))
    status = await router.provider_status()
    blob = str(status)
    assert "test-gemini-key-xyz" not in blob
    assert "test-cerebras-key-xyz" not in blob


def test_startup_neither_provider_enabled(client, monkeypatch):
    # Neither provider enabled -> honest deterministic startup contract.
    monkeypatch.setattr(settings, "GEMINI_ENABLED", False)
    monkeypatch.setattr(settings, "CEREBRAS_ENABLED", False)
    monkeypatch.setattr(settings, "GEMINI_API_KEY", "")
    monkeypatch.setattr(settings, "CEREBRAS_API_KEY", "")
    monkeypatch.setattr(settings, "DETERMINISTIC_MODE", True)
    reset_router()
    r = client.get("/api/v1/system/status")
    assert r.status_code == 200
    body = r.json()
    assert body["mode"] == "deterministic"
    assert body["providers"]["gemini"]["available"] is False
    assert body["providers"]["cerebras"]["available"] is False
    ledger = body["providers"]["cerebras_ledger"]
    assert ledger["budget_usd"] == 4.00
    assert "dashboard is authoritative" in ledger["balance_note"]


def test_obligation_extract_honest_without_providers(client):
    r = client.post("/api/v1/regulations", json={"code": "REG-LLM1", "title": "LLM honesty probe regulation"})
    reg_id = r.json()["id"]
    v = client.post(f"/api/v1/regulations/{reg_id}/versions", json={"version_label": "v1"}).json()
    d = client.post(
        "/api/v1/documents",
        files={"file": ("r.txt", b"The bank shall retain records.", "text/plain")},
        data={"source_kind": "synthetic"},
    ).json()
    ex = client.post(
        "/api/v1/obligations/extract",
        json={"regulation_version_id": v["id"], "document_id": d["document_id"], "use_llm": True},
    )
    assert ex.status_code == 200
    body = ex.json()
    assert body["method"] == "deterministic"
    assert body["execution_mode"] in ("deterministic", "deterministic_fallback")
    assert body["provider"] is None
    assert any("deterministic" in w for w in body["warnings"])
