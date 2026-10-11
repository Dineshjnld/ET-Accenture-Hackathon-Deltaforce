"""Secret redaction for logs and error messages.

API keys must never reach the browser, the logs, or test output.
"""

from __future__ import annotations

import re

_PATTERNS: list[re.Pattern[str]] = [
    # Gemini keys (AIza...) and Cerebras keys (csk-... / sk-...)
    re.compile(r"AIza[0-9A-Za-z\-_]{10,}"),
    re.compile(r"\b(csk|sk)-(live|test)?-?[A-Za-z0-9\-_]{8,}\b"),
    re.compile(r"(?i)(api[_-]?key\s*[:=]\s*)(['\"]?)[A-Za-z0-9\-_/.+=]{8,}\2"),
    re.compile(r"(?i)(bearer\s+)[A-Za-z0-9\-_/.+=]{8,}"),
    re.compile(r"(?i)(GEMINI_API_KEY|CEREBRAS_API_KEY|JWT_SECRET)\s*=\s*[^,\s}\]]+"),
]

_REDACTED = "[REDACTED]"


def redact(text: str | None) -> str:
    """Return *text* with anything that looks like a credential removed."""
    if not text:
        return ""
    out = str(text)
    for pattern in _PATTERNS:
        out = pattern.sub(
            lambda m: m.group(0)[:0] + _REDACTED if m.lastindex is None else f"{m.group(1)}{_REDACTED}",
            out,
        )
    return out


def redact_mapping(data: dict) -> dict:
    """Redact string values in a shallow mapping (for status payloads)."""
    return {k: (redact(v) if isinstance(v, str) else v) for k, v in data.items()}


__all__ = ["redact", "redact_mapping"]
