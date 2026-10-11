"""Security helpers: file validation, password hashing, JWT issue/verify."""
from __future__ import annotations

import hashlib
import os
import re
import secrets
import uuid
from datetime import timedelta
from pathlib import Path, PurePosixPath
from typing import Any

import jwt
from passlib.context import CryptContext

from app.config import settings
from app.models.core import utcnow

_pwd = CryptContext(schemes=["bcrypt"], deprecated="auto")

# magic-byte signatures for the formats we accept
_SIGNATURES: dict[str, bytes] = {
    "application/pdf": b"%PDF-",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": b"PK\x03\x04",
    "application/msword": b"\xd0\xcf\x11\xe0",
}


class SecurityError(Exception):
    """Raised for rejected uploads or failed authorisation."""


# ---------------------------------------------------------------------------
# Files
# ---------------------------------------------------------------------------
def validate_upload(filename: str, declared_type: str, size_bytes: int, head: bytes) -> str:
    """Validate an in-memory upload. Returns the normalised file extension."""
    if size_bytes <= 0:
        raise SecurityError("Empty file rejected")
    if size_bytes > settings.max_upload_bytes:
        raise SecurityError(
            f"File exceeds the {settings.MAX_UPLOAD_MB} MB limit"
        )
    safe_name = os.path.basename(filename or "")
    if not safe_name or safe_name in (".", ".."):
        raise SecurityError("Missing filename")
    if "\x00" in safe_name or "/" in safe_name or "\\" in safe_name:
        raise SecurityError("Illegal characters in filename")

    ext = Path(safe_name).suffix.lower()
    if ext not in settings.ALLOWED_EXTENSIONS:
        raise SecurityError(
            f"File type '{ext or safe_name}' is not permitted. Allowed: "
            f"{', '.join(settings.ALLOWED_EXTENSIONS)}"
        )

    expected_ct = _EXTENSION_CONTENT_TYPE.get(ext)
    if expected_ct and declared_type and declared_type != expected_ct and declared_type != "application/octet-stream":
        # content type mismatch is tolerated only when the magic bytes match
        magic = _SIGNATURES.get(expected_ct)
        if magic and not head.startswith(magic):
            raise SecurityError("Declared content type does not match file content")

    magic = _SIGNATURES.get(expected_ct or "")
    if magic and head and not head.startswith(magic):
        raise SecurityError("File content does not match its extension (magic byte check failed)")

    if expected_ct == "text/plain" and b"\x00" in head:
        raise SecurityError("Binary content is not permitted in a text file")

    return ext


_EXTENSION_CONTENT_TYPE = {
    ".pdf": "application/pdf",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".txt": "text/plain",
    ".md": "text/plain",
    ".csv": "text/csv",
    ".json": "application/json",
    ".html": "text/html",
    ".htm": "text/html",
}


def safe_stored_filename(original: str) -> str:
    """Generate a server-side filename: uuid + original extension."""
    ext = Path(os.path.basename(original or "")).suffix.lower()
    if ext not in settings.ALLOWED_EXTENSIONS:
        ext = ".bin"
    token = uuid.uuid4().hex
    digest = hashlib.sha256(secrets.token_bytes(16)).hexdigest()[:8]
    return f"{token}-{digest}{ext}"


def resolve_within(base: Path, relative: str) -> Path:
    """Resolve ``relative`` inside ``base``, refusing path traversal."""
    base = base.resolve()
    target = (base / PurePosixPath(relative).name).resolve()
    if base != target and base not in target.parents:
        raise SecurityError("Path traversal attempt rejected")
    return target


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


# ---------------------------------------------------------------------------
# Passwords / JWT
# ---------------------------------------------------------------------------
def hash_password(password: str) -> str:
    return _pwd.hash(password)


def verify_password(password: str, hashed: str | None) -> bool:
    if not hashed:
        return False
    try:
        return _pwd.verify(password, hashed)
    except Exception:  # pragma: no cover
        return False


def create_access_token(subject: str, role: str, extra: dict[str, Any] | None = None) -> str:
    now = utcnow()
    payload: dict[str, Any] = {
        "sub": subject,
        "role": role,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(minutes=settings.JWT_EXPIRE_MINUTES)).timestamp()),
        "jti": secrets.token_urlsafe(8),
    }
    if extra:
        payload.update(extra)
    return jwt.encode(payload, settings.JWT_SECRET, algorithm=settings.JWT_ALGORITHM)


def decode_token(token: str) -> dict[str, Any]:
    try:
        return jwt.decode(token, settings.JWT_SECRET, algorithms=[settings.JWT_ALGORITHM])
    except Exception as exc:
        raise SecurityError(f"Invalid token: {exc}") from exc


# ---------------------------------------------------------------------------
# Untrusted-input guards
# ---------------------------------------------------------------------------
_INJECTION_PATTERNS = [
    re.compile(r"ignore\s+(all|any|the)?\s*(previous|prior|above)\s+instructions", re.I),
    re.compile(r"disregard\s+(the\s+)?(system|previous)\s+prompt", re.I),
    re.compile(r"you\s+are\s+now\s+(a|an|in)\s+", re.I),
    re.compile(r"\b(system|assistant)\s*:\s*", re.I),
    re.compile(r"```\s*(system|assistant)", re.I),
    re.compile(r"</?(system|instruction|tool_call|function_call)>", re.I),
    re.compile(r"(reveal|print|expose|show)\s+(me\s+)?(your\s+)?(system\s+prompt|api[_\s]?key|secrets?)", re.I),
]


def detect_prompt_injection(text: str) -> list[str]:
    """Return human-readable descriptions of suspicious patterns found in
    *untrusted* document text. Used to flag, never to silently drop content."""
    hits: list[str] = []
    for pattern in _INJECTION_PATTERNS:
        match = pattern.search(text or "")
        if match:
            hits.append(f"possible prompt-injection pattern near {match.start()} bytes: {match.group(0)[:60]!r}")
    return hits
