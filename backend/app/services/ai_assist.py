"""AI-assisted helpers — the ONLY place services touch hosted models.

Every function here:
* calls get_router().generate_for_task_sync with a single bounded prompt
  (never parallel, never unbounded, cached when repeatable);
* validates structured output with Pydantic (malformed -> rejected);
* verifies citations against supplied source text (non-verbatim -> warning);
* returns an explicit execution_mode so API/UI never mislabels output.

Deterministic pipelines remain the ground truth. AI results are candidates
requiring human approval — never silently promoted.
"""

from __future__ import annotations

from typing import Any

from app.llm.errors import LLMError
from app.llm.prompts import SYSTEM_PREAMBLE, build_mapping_prompt, build_obligation_prompt
from app.llm.redaction import redact
from app.llm.router import get_router
from app.llm.schemas import InterpretationResult, MappingList, ObligationList, RemediationList
from app.logging_config import get_logger

log = get_logger("regulens.ai_assist")


def _call(task: str, prompt: str, response_model: Any, system: str | None = None) -> dict[str, Any]:
    """Single bounded router call. Returns dict with execution metadata."""
    router = get_router()
    try:
        resp = router.generate_for_task_sync(
            task, prompt, system=system or SYSTEM_PREAMBLE, response_model=response_model
        )
        return {
            "ok": True,
            "parsed": resp.parsed_json,
            "execution_mode": resp.execution_mode if not resp.cached else "cache",
            "provider": resp.provider,
            "model": resp.model,
            "warnings": list(resp.warnings or []),
            "usage": {"input_tokens": resp.usage.input_tokens, "output_tokens": resp.usage.output_tokens},
            "cost_usd": resp.cost_usd,
            "cached": resp.cached,
        }
    except LLMError as exc:
        log.warning("ai_assist.unavailable", task=task, error=redact(str(exc))[:200])
        return {
            "ok": False,
            "parsed": None,
            "execution_mode": "deterministic_fallback",
            "provider": "none",
            "model": None,
            "warnings": [
                f"AI-assisted {task} unavailable ({redact(str(exc))[:160]}). "
                "Deterministic output used; no model output claimed."
            ],
            "usage": {"input_tokens": 0, "output_tokens": 0},
            "cost_usd": 0.0,
            "cached": False,
        }


def extract_obligations_ai(text: str, source: str) -> dict[str, Any]:
    """Gemini obligation extraction over a BOUNDED sample (router policy)."""
    prompt = build_obligation_prompt(text, source)
    return _call("obligation_extraction", prompt, ObligationList)


def propose_mapping_ai(obligation_text: str, controls: list[dict[str, Any]]) -> dict[str, Any]:
    """Cerebras candidate mapping over bounded candidates (router policy)."""
    prompt = build_mapping_prompt(obligation_text, controls)
    return _call("control_mapping", prompt, MappingList)


def propose_remediation_ai(gap_summaries: list[dict[str, Any]]) -> dict[str, Any]:
    import json

    prompt = (
        "Propose concise remediation drafts for the compliance gaps below. "
        'Return JSON {"remediations": [{"proposed_action": ..., "reason": ..., '
        '"suggested_owner_role": ..., "verification": ..., "confidence": 0-1, "warnings": [...]}]}. '
        "Do NOT invent costs, durations, deadlines or control ids. Flag uncertainty in warnings.\n\n"
        f"<gaps>\n{json.dumps(gap_summaries, indent=2)[:12000]}\n</gaps>\n\nReturn JSON only."
    )
    return _call("remediation_proposals", prompt, RemediationList)


def interpret_changes_ai(summary: dict[str, Any], changes: list[dict[str, Any]]) -> dict[str, Any]:
    import json

    comparison = json.dumps({"summary": summary, "changes": changes[:30]}, indent=2, default=str)[:12000]
    prompt = (
        "Interpret the regulatory version comparison below for a compliance officer. "
        'Return JSON {"summary": ..., "key_points": [...], "citations": [...], '
        '"confidence": 0-1, "requires_human_review": true, "warnings": [...]}. '
        "Never invent clause numbers or dates; cite only supplied change summaries.\n\n"
        f"<comparison>\n{comparison}\n</comparison>"
    )
    return _call("version_comparison", prompt, InterpretationResult)


__all__ = [
    "extract_obligations_ai",
    "propose_mapping_ai",
    "propose_remediation_ai",
    "interpret_changes_ai",
]
