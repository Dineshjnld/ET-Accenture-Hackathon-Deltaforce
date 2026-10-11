"""Document ingestion service.

Pipeline: validate -> store -> parse -> persist metadata -> chunk -> (optional)
embed. Every step is local; no network calls and no paid parsing service.
"""
from __future__ import annotations

import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.errors import ParseFailedError, ValidationError
from app.logging_config import get_logger
from app.models.core import DocumentSourceKind, ReviewStatus
from app.models.regulatory import Document, DocumentChunk, DocumentVersion
from app.parsing import ParsedDocument, parse_document
from app.security import (
    SecurityError,
    detect_prompt_injection,
    safe_stored_filename,
    sha256_bytes,
    validate_upload,
)
from app.services.audit import record_audit

log = get_logger("regulens.ingestion")

CHUNK_TARGET_CHARS = 2200
CHUNK_MAX_CHARS = 3200
_CLAUSE_SPLIT = re.compile(r"(?m)^\s*(?:\(?[0-9]{1,3}[\.\)]|[ivxlcdm]{1,7}[\.\)]|[A-Z][\.\)])\s+")


@dataclass
class IngestionResult:
    document_id: str
    document_uid: str
    filename: str
    page_count: int
    text_length: int
    chunk_count: int
    ocr_used: bool
    duplicate_of: str | None
    warnings: list[str]
    chunks: list[DocumentChunk]


def store_upload(
    session: Session,
    *,
    filename: str,
    content_type: str,
    data: bytes,
    actor: str = "system",
    org_slug: str = "default",
    **doc_fields: Any,
) -> IngestionResult:
    """Validate, store and ingest an uploaded document."""
    head = data[:8192]
    try:
        ext = validate_upload(filename, content_type, len(data), head)
    except SecurityError as exc:
        raise ValidationError(str(exc)) from exc

    digest = sha256_bytes(data)
    existing = session.scalar(select(Document).where(Document.sha256 == digest))
    if existing is not None:
        return IngestionResult(
            document_id=existing.id,
            document_uid=existing.stored_filename,
            filename=existing.filename,
            page_count=existing.page_count or 0,
            text_length=existing.text_length,
            chunk_count=len(existing.chunks),
            ocr_used=existing.ocr_used,
            duplicate_of=existing.id,
            warnings=["Duplicate content — this document has already been ingested."],
            chunks=list(existing.chunks),
        )

    target_dir = settings.upload_dir_for(org_slug)
    stored = safe_stored_filename(filename)
    target = target_dir / stored
    target.write_bytes(data)

    return ingest_path(
        session,
        path=target,
        original_filename=filename,
        content_type=content_type,
        size_bytes=len(data),
        sha256=digest,
        actor=actor,
        stored_filename=stored,
        **doc_fields,
    )


def ingest_path(
    session: Session,
    *,
    path: Path,
    original_filename: str | None = None,
    content_type: str | None = None,
    size_bytes: int | None = None,
    sha256: str | None = None,
    actor: str = "system",
    stored_filename: str | None = None,
    version_label: str = "v1",
    source_kind: DocumentSourceKind = DocumentSourceKind.INTERNAL,
    **doc_fields: Any,
) -> IngestionResult:
    """Parse a file already on disk and persist document + chunks."""
    path = Path(path)
    filename = original_filename or path.name
    started = time.perf_counter()

    try:
        parsed: ParsedDocument = parse_document(path)
    except ParseFailedError:
        raise
    except Exception as exc:
        log.warning("ingestion.parse_failed", file=path.name, error=str(exc))
        raise ParseFailedError(f"Could not parse {filename}: {exc}") from exc

    data = path.read_bytes()
    digest = sha256 or sha256_bytes(data)

    warnings: list[str] = list(parsed.warnings)
    injection_hits = detect_prompt_injection(parsed.text)
    if injection_hits:
        warnings.extend(injection_hits)
        log.warning("ingestion.suspicious_content", file=filename, hits=injection_hits[:3])

    source_kind = doc_fields.pop("source_kind", source_kind)
    source_url = doc_fields.pop("source_url", None)
    document_type = doc_fields.pop("document_type", None) or _guess_document_type(filename, parsed)
    document = Document(
        filename=filename,
        stored_filename=stored_filename or path.name,
        content_type=content_type or "application/octet-stream",
        size_bytes=size_bytes if size_bytes is not None else len(data),
        sha256=digest,
        source_kind=source_kind,
        source_url=source_url,
        document_type=document_type,
        page_count=parsed.page_count or (1 if parsed.text else 0),
        text_length=len(parsed.text),
        extraction_status="ok" if parsed.text.strip() else "empty",
        extraction_error=None if parsed.text.strip() else "No extractable text",
        ocr_used=parsed.ocr_used,
        parse_duration_ms=int((time.perf_counter() - started) * 1000),
        uploaded_by=actor,
        parsed_at=_utcnow(),
        **doc_fields,
    )
    session.add(document)
    session.flush()

    text_path = _persist_text(document.stored_filename, parsed.text)
    version = DocumentVersion(
        document_id=document.id,
        version_label=version_label,
        sha256=digest,
        published_at=document.published_at,
        effective_from=document.published_at,
        text_path=str(text_path) if text_path else None,
        text_length=len(parsed.text),
        change_summary="Initial ingestion",
        created_by=actor,
    )
    session.add(version)
    session.flush()

    blocks = chunk_document(document.id, version.id, parsed)
    session.add_all(blocks)
    session.flush()

    record_audit(
        session,
        actor=actor,
        action="document.ingested",
        entity_type="document",
        entity_id=document.id,
        after={
            "filename": document.filename,
            "source_kind": str(source_kind),
            "source_url": source_url,
            "sha256": digest,
            "chunks": len(blocks),
        },
        detail="; ".join(warnings) if warnings else None,
    )

    return IngestionResult(
        document_id=document.id,
        document_uid=document.stored_filename,
        filename=document.filename,
        page_count=document.page_count or 0,
        text_length=document.text_length,
        chunk_count=len(blocks),
        ocr_used=document.ocr_used,
        duplicate_of=None,
        warnings=warnings,
        chunks=blocks,
    )


def _utcnow():
    from datetime import datetime, timezone

    return datetime.now(timezone.utc)


def _persist_text(stored_filename: str, text: str) -> Path | None:
    if not text.strip():
        return None
    base = settings.UPLOAD_DIR / "_text"
    base.mkdir(parents=True, exist_ok=True)
    path = base / f"{stored_filename}.txt"
    path.write_text(text, encoding="utf-8")
    return path


def _guess_document_type(filename: str, parsed: ParsedDocument) -> str:
    name = filename.lower()
    if "master direction" in name or "direction" in name:
        return "master_direction"
    if "circular" in name:
        return "circular"
    if "notification" in name:
        return "notification"
    if any(k in name for k in ("policy", "sop", "manual")):
        return "internal_policy"
    if any(k in name for k in ("rcm", "control", "matrix")):
        return "control_matrix"
    if any(k in name for k in ("audit", "report", "test")):
        return "audit_report"
    if parsed.metadata.get("Title"):
        return "document"
    return "document"


# ---------------------------------------------------------------------------
# Chunking
# ---------------------------------------------------------------------------
def chunk_document(document_id: str, version_id: str, parsed: ParsedDocument) -> list[DocumentChunk]:
    blocks = parsed.blocks or [ParsedBlock(text=parsed.text)]
    chunks: list[DocumentChunk] = []
    buffer: list[str] = []
    buffer_pages: list[int] = []
    buffer_sections: list[str] = []
    buffer_len = 0
    index = 0

    def flush() -> None:
        nonlocal index, buffer, buffer_pages, buffer_sections, buffer_len
        if not buffer:
            return
        text = "\n\n".join(buffer).strip()
        if text:
            chunks.append(
                DocumentChunk(
                    document_id=document_id,
                    document_version_id=version_id,
                    chunk_index=index,
                    page=buffer_pages[0] if buffer_pages else None,
                    section=_join_sections(buffer_sections),
                    heading=None,
                    text=text[:60000],
                    char_count=len(text),
                )
            )
            index += 1
        buffer, buffer_pages, buffer_sections, buffer_len = [], [], [], 0

    current_section: str | None = None
    for block in blocks:
        if block.kind == "heading":
            current_section = block.text.strip()[:250]
        parts = _split_long(block.text)
        for part in parts:
            if buffer_len + len(part) > CHUNK_MAX_CHARS and buffer:
                flush()
            buffer.append(part)
            buffer_len += len(part)
            if block.page and block.page not in buffer_pages:
                buffer_pages.append(block.page)
            if block.section or current_section:
                buffer_sections.append(block.section or current_section or "")
            if buffer_len >= CHUNK_TARGET_CHARS:
                flush()
    flush()
    return chunks


def _split_long(text: str) -> list[str]:
    text = text.strip()
    if len(text) <= CHUNK_MAX_CHARS:
        return [text] if text else []
    pieces: list[str] = []
    sentences = re.split(r"(?<=[.;:])\s+", text)
    current = ""
    for sentence in sentences:
        if len(current) + len(sentence) > CHUNK_MAX_CHARS and current:
            pieces.append(current)
            current = sentence
        else:
            current = f"{current} {sentence}".strip()
    if current:
        pieces.append(current)
    return pieces


def _join_sections(sections: list[str]) -> str | None:
    seen: list[str] = []
    for s in sections:
        s = (s or "").strip()
        if s and s not in seen:
            seen.append(s)
    return " | ".join(seen)[:500] if seen else None


def embed_chunks(session: Session, chunks: list[DocumentChunk]) -> int:
    """Best-effort embedding. Returns the number of chunks embedded."""
    from app.llm.embeddings import get_embedder

    embedder = get_embedder()
    status = embedder.status()
    if not status.get("available"):
        log.info("ingestion.embeddings_skipped", reason=status.get("reason", "disabled"))
        return 0
    done = 0
    batch: list[DocumentChunk] = []
    texts: list[str] = []
    for chunk in chunks:
        if chunk.embedding:
            continue
        batch.append(chunk)
        texts.append(chunk.text[:8000])
        if len(batch) >= 16:
            done += _apply_embeddings(embedder, batch, texts, status)
            batch, texts = [], []
    if batch:
        done += _apply_embeddings(embedder, batch, texts, status)
    return done


def _apply_embeddings(embedder: Any, batch: list[DocumentChunk], texts: list[str], status: dict) -> int:
    try:
        vectors = embedder.embed(texts)
    except Exception as exc:
        log.warning("ingestion.embedding_batch_failed", error=str(exc))
        return 0
    for chunk, vector in zip(batch, vectors):
        chunk.embedding = vector
        chunk.embedding_model = f"{status.get('provider')}:{status.get('model', '')}".rstrip(":")
    session.flush()
    return len(batch)


__all__ = [
    "ingest_path",
    "store_upload",
    "chunk_document",
    "embed_chunks",
    "IngestionResult",
]
