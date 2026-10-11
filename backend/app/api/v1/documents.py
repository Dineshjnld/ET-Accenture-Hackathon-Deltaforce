"""Documents, regulations, obligations endpoints."""
from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import RequestContext, get_context, get_db, page_response
from app.errors import NotFoundError, ValidationError
from app.logging_config import get_logger
from app.models.core import DocumentSourceKind, ReviewStatus
from app.models.obligations import Obligation
from app.models.regulatory import Document, DocumentChunk, DocumentVersion, Regulation, RegulationVersion
from app.schemas import (
    ChangeOut,
    CompareVersionsRequest,
    CompareVersionsResponse,
    ControlOut,
    DocumentDetail,
    DocumentOut,
    DocumentVersionOut,
    ExtractObligationsRequest,
    IngestSampleRequest,
    IngestSampleResponse,
    MappingOut,
    ObligationApproveRequest,
    ObligationDetail,
    ObligationOut,
    RegulationCreate,
    RegulationDetail,
    RegulationOut,
    RegulationVersionCreate,
    RegulationVersionOut,
)
from app.security import SecurityError, sha256_bytes
from app.services.audit import record_audit
from app.services.ingestion import IngestionResult, store_upload

router = APIRouter()
log = get_logger("regulens.api.documents")


# ---------------------------------------------------------------------------
# Documents
# ---------------------------------------------------------------------------
def _to_document_out(doc: Document) -> DocumentOut:
    return DocumentOut.model_validate(doc)


@router.post("/documents", response_model=IngestSampleResponse, status_code=201)
async def upload_document(
    file: UploadFile = File(...),
    source_kind: str = Form("internal"),
    source_url: str | None = Form(None),
    publisher: str | None = Form(None),
    published_at: str | None = Form(None),
    jurisdiction: str | None = Form(None),
    reference_number: str | None = Form(None),
    version_label: str = Form("v1"),
    actor: str = Form("compliance.officer"),
    db: Session = Depends(get_db),
    ctx: RequestContext = Depends(get_context),
) -> IngestSampleResponse:
    """Upload and ingest a regulatory or internal document.

    Accepts PDF, DOCX, TXT, Markdown, CSV, JSON, HTML. Files are validated by
    extension, declared content type and magic bytes, stored with a server-side
    generated name outside any executable directory, then parsed locally.
    """
    data = await file.read()
    from datetime import date

    published: date | None = None
    if published_at:
        try:
            published = date.fromisoformat(published_at)
        except ValueError as exc:
            raise ValidationError("published_at must be ISO-8601 (YYYY-MM-DD)") from exc

    try:
        result = store_upload(
            db,
            filename=file.filename or "upload",
            content_type=file.content_type or "application/octet-stream",
            data=data,
            actor=actor,
            source_kind=_resolve_source_kind(source_kind),
            source_url=source_url,
            publisher=publisher,
            published_at=published,
            jurisdiction=jurisdiction,
            reference_number=reference_number,
            version_label=version_label,
        )
    except SecurityError as exc:
        raise ValidationError(str(exc)) from exc

    record_audit(
        db,
        actor=actor,
        action="document.uploaded",
        entity_type="document",
        entity_id=result.document_id,
        entity_uid=result.document_uid,
        correlation_id=ctx.correlation_id,
        request_path=ctx.path,
        request_method=ctx.method,
        ip_address=ctx.ip,
        after={"filename": result.filename, "size": len(data)},
    )
    return IngestSampleResponse(
        document_id=result.document_id,
        filename=result.filename,
        page_count=result.page_count,
        text_length=result.text_length,
        chunk_count=result.chunk_count,
        ocr_used=result.ocr_used,
        duplicate_of=result.duplicate_of,
        warnings=result.warnings,
    )


def _resolve_source_kind(value: str) -> DocumentSourceKind:
    try:
        return DocumentSourceKind(value)
    except ValueError as exc:
        raise ValidationError(
            f"source_kind must be one of {[k.value for k in DocumentSourceKind]}"
        ) from exc


@router.get("/documents", response_model=dict)
def list_documents(
    db: Session = Depends(get_db),
    ctx: RequestContext = Depends(get_context),
    source_kind: str | None = None,
    document_type: str | None = None,
    jurisdiction: str | None = None,
    search: str | None = None,
    page: int = 1,
    page_size: int = 25,
    sort: str | None = "created_at",
    order: str = "desc",
) -> dict[str, Any]:
    stmt = select(Document).where(Document.is_archived.is_(False))
    if source_kind:
        stmt = stmt.where(Document.source_kind == _resolve_source_kind(source_kind))
    if document_type:
        stmt = stmt.where(Document.document_type == document_type)
    if jurisdiction:
        stmt = stmt.where(Document.jurisdiction == jurisdiction)
    if search:
        stmt = stmt.where(Document.filename.ilike(f"%{search}%"))
    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    column = getattr(Document, sort or "created_at", Document.created_at)
    stmt = stmt.order_by(column.desc() if order == "desc" else column.asc())
    stmt = stmt.offset((page - 1) * page_size).limit(page_size)
    items = [_to_document_out(d).model_dump(mode="json") for d in db.scalars(stmt)]
    return page_response(items, total, _params(page, page_size))


def _params(page: int, page_size: int):
    from app.schemas import PageParams

    return PageParams(page=page, page_size=page_size)


@router.get("/documents/{document_id}", response_model=DocumentDetail)
def get_document(
    document_id: str, db: Session = Depends(get_db), ctx: RequestContext = Depends(get_context)
) -> DocumentDetail:
    doc = db.get(Document, document_id)
    if doc is None:
        raise NotFoundError(f"Document {document_id} not found")
    return DocumentDetail.model_validate(doc)


@router.get("/documents/{document_id}/versions", response_model=list[DocumentVersionOut])
def get_document_versions(
    document_id: str, db: Session = Depends(get_db), ctx: RequestContext = Depends(get_context)
) -> list[DocumentVersionOut]:
    doc = db.get(Document, document_id)
    if doc is None:
        raise NotFoundError(f"Document {document_id} not found")
    return [DocumentVersionOut.model_validate(v) for v in doc.versions]


@router.get("/documents/{document_id}/chunks", response_model=list[dict])
def get_document_chunks(
    document_id: str,
    page: int = 1,
    page_size: int = 25,
    db: Session = Depends(get_db),
    ctx: RequestContext = Depends(get_context),
) -> list[dict]:
    doc = db.get(Document, document_id)
    if doc is None:
        raise NotFoundError(f"Document {document_id} not found")
    stmt = (
        select(DocumentChunk)
        .where(DocumentChunk.document_id == document_id)
        .order_by(DocumentChunk.chunk_index)
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    return [
        {
            "id": c.id,
            "chunk_index": c.chunk_index,
            "page": c.page,
            "section": c.section,
            "heading": c.heading,
            "char_count": c.char_count,
            "text": c.text,
        }
        for c in db.scalars(stmt)
    ]


@router.post("/documents/ingest-path", response_model=IngestSampleResponse, status_code=201)
def ingest_from_path(
    payload: IngestSampleRequest, db: Session = Depends(get_db), ctx: RequestContext = Depends(get_context)
) -> IngestSampleResponse:
    """Ingest a document already on disk (used for the bundled RBI sample files)."""
    from pathlib import Path

    from app.parsing import ParseError
    from app.services.ingestion import ingest_path

    path = Path(payload.path)
    if not path.is_absolute():
        path = (Path.cwd() / path).resolve()
    if not path.exists():
        raise NotFoundError(f"Path not found: {path}")
    allowed_root = get_settings_upload_root()
    try:
        path.resolve().relative_to(allowed_root)
    except ValueError:
        raise ValidationError(
            "Only files inside the configured sample/upload directories can be ingested by path."
        ) from None
    try:
        result = ingest_path(
            db,
            path=path,
            original_filename=payload.filename or path.name,
            content_type="application/octet-stream",
            actor=ctx.actor,
            source_kind=DocumentSourceKind(payload.source_kind),
            source_url=payload.source_url,
            publisher=payload.publisher,
            published_at=payload.published_at,
            jurisdiction=payload.jurisdiction,
            reference_number=payload.reference_number,
            version_label=payload.version_label,
        )
    except ParseError as exc:
        raise ValidationError(str(exc)) from exc
    return _ingest_response(result)


def get_settings_upload_root():
    from app.config import settings

    return settings.UPLOAD_DIR.parent


def _ingest_response(result: IngestionResult) -> IngestSampleResponse:
    return IngestSampleResponse(
        document_id=result.document_id,
        filename=result.filename,
        page_count=result.page_count,
        text_length=result.text_length,
        chunk_count=result.chunk_count,
        ocr_used=result.ocr_used,
        duplicate_of=result.duplicate_of,
        warnings=result.warnings,
    )


# ---------------------------------------------------------------------------
# Regulations
# ---------------------------------------------------------------------------
@router.post("/regulations", response_model=RegulationOut, status_code=201)
def create_regulation(
    payload: RegulationCreate, db: Session = Depends(get_db), ctx: RequestContext = Depends(get_context)
) -> RegulationOut:
    existing = db.scalar(select(Regulation).where(Regulation.code == payload.code))
    if existing:
        from app.errors import ConflictError

        raise ConflictError(f"Regulation code {payload.code} already exists")
    regulation = Regulation(**payload.model_dump(exclude={"source_name", "source_url"}))
    if payload.source_id:
        regulation.source_id = payload.source_id
    elif payload.source_name:
        from app.models.regulatory import RegulatorySource

        source = db.scalar(select(RegulatorySource).where(RegulatorySource.name == payload.source_name))
        if source is None:
            source = RegulatorySource(
                name=payload.source_name,
                authority=payload.regulator,
                jurisdiction=payload.jurisdiction,
                source_url=payload.source_url,
            )
            db.add(source)
            db.flush()
        regulation.source_id = source.id
    db.add(regulation)
    db.flush()
    record_audit(
        db,
        actor=ctx.actor,
        action="regulation.created",
        entity_type="regulation",
        entity_id=regulation.id,
        entity_uid=regulation.code,
        correlation_id=ctx.correlation_id,
        after=payload.model_dump(mode="json"),
    )
    return RegulationOut.model_validate(regulation)


@router.get("/regulations", response_model=dict)
def list_regulations(
    db: Session = Depends(get_db),
    ctx: RequestContext = Depends(get_context),
    jurisdiction: str | None = None,
    category: str | None = None,
    search: str | None = None,
    page: int = 1,
    page_size: int = 25,
    sort: str = "created_at",
    order: str = "desc",
) -> dict[str, Any]:
    stmt = select(Regulation)
    if jurisdiction:
        stmt = stmt.where(Regulation.jurisdiction == jurisdiction)
    if category:
        stmt = stmt.where(Regulation.category == category)
    if search:
        stmt = stmt.where(Regulation.title.ilike(f"%{search}%"))
    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    column = getattr(Regulation, sort, Regulation.created_at)
    stmt = stmt.order_by(column.desc() if order == "desc" else column.asc())
    stmt = stmt.offset((page - 1) * page_size).limit(page_size)
    items = [RegulationOut.model_validate(r).model_dump(mode="json") for r in db.scalars(stmt)]
    return page_response(items, total, _params(page, page_size))


@router.get("/regulations/{regulation_id}", response_model=RegulationDetail)
def get_regulation(
    regulation_id: str, db: Session = Depends(get_db), ctx: RequestContext = Depends(get_context)
) -> RegulationDetail:
    regulation = db.get(Regulation, regulation_id)
    if regulation is None:
        raise NotFoundError(f"Regulation {regulation_id} not found")
    return RegulationDetail.model_validate(regulation)


@router.post("/regulations/{regulation_id}/versions", response_model=RegulationVersionOut, status_code=201)
def create_regulation_version(
    regulation_id: str,
    payload: RegulationVersionCreate,
    db: Session = Depends(get_db),
    ctx: RequestContext = Depends(get_context),
) -> RegulationVersionOut:
    regulation = db.get(Regulation, regulation_id)
    if regulation is None:
        raise NotFoundError(f"Regulation {regulation_id} not found")
    version = RegulationVersion(regulation_id=regulation_id, **payload.model_dump())
    if payload.is_current:
        for existing in regulation.versions:
            existing.is_current = False
    db.add(version)
    db.flush()
    record_audit(
        db,
        actor=ctx.actor,
        action="regulation.version_created",
        entity_type="regulation_version",
        entity_id=version.id,
        entity_uid=version.version_label,
        correlation_id=ctx.correlation_id,
        after=payload.model_dump(mode="json"),
    )
    return RegulationVersionOut.model_validate(version)


@router.post("/regulations/{regulation_id}/compare-versions", response_model=CompareVersionsResponse)
def compare_versions(
    regulation_id: str,
    payload: CompareVersionsRequest,
    db: Session = Depends(get_db),
    ctx: RequestContext = Depends(get_context),
) -> CompareVersionsResponse:
    """Compare two versions of a regulation and record candidate changes."""
    from app.services.diffing import compare_versions as _compare

    regulation = db.get(Regulation, regulation_id)
    if regulation is None:
        raise NotFoundError(f"Regulation {regulation_id} not found")
    try:
        result, changes = _compare(
            db,
            regulation_id=regulation_id,
            from_version_id=payload.from_version_id,
            to_version_id=payload.to_version_id,
            actor=payload.actor,
            correlation_id=ctx.correlation_id,
        )
    except ValueError as exc:
        raise ValidationError(str(exc)) from exc

    workflow_result = None
    if payload.run_workflow and changes:
        from app.agents import run_workflow

        workflow_result = run_workflow(
            db,
            "change_impact",
            {"regulation_id": regulation_id, "change_id": changes[0].id},
            actor=ctx.actor,
        )

    record_audit(
        db,
        actor=ctx.actor,
        action="regulation.versions_compared_api",
        entity_type="regulation",
        entity_id=regulation_id,
        correlation_id=ctx.correlation_id,
        request_path=ctx.path,
        request_method=ctx.method,
        after={"changes": len(changes), "by_type": result.summary["by_type"]},
    )
    return CompareVersionsResponse(
        comparison_id=f"{result.left_label}->{result.right_label}",
        left_label=result.left_label,
        right_label=result.right_label,
        provisions_left=result.provisions_left,
        provisions_right=result.provisions_right,
        changes=[ChangeOut.model_validate(c) for c in changes],
        summary=result.summary,
        workflow=workflow_result,
    )


# ---------------------------------------------------------------------------
# Obligations
# ---------------------------------------------------------------------------
@router.post("/obligations/extract", response_model=dict)
def extract_obligations(
    payload: ExtractObligationsRequest,
    db: Session = Depends(get_db),
    ctx: RequestContext = Depends(get_context),
) -> dict[str, Any]:
    from app.services.obligations import persist_obligations

    version = db.get(RegulationVersion, payload.regulation_version_id)
    if version is None:
        raise NotFoundError("Regulation version not found")
    document = db.get(Document, payload.document_id)
    if document is None:
        raise NotFoundError("Document not found")

    warnings: list[str] = []
    method = "deterministic"
    execution_mode = "deterministic"
    provider_used: str | None = None
    model_used = None
    ai_created: list = []
    if payload.use_llm:
        from app.config import settings
        from app.llm.router import get_router

        if settings.effective_deterministic:
            warnings.append(
                "use_llm=true but no hosted provider is enabled/configured (or "
                "DETERMINISTIC_MODE=true) — deterministic extraction was used and "
                "no model output is claimed."
            )
        else:
            router = get_router()
            preferred = router.provider_for_task("obligation_extraction")
            provider = router._provider(preferred)
            if not provider.available():
                warnings.append(
                    f"use_llm=true but the routed provider '{preferred}' is not available — "
                    "deterministic extraction was used and no model output is claimed."
                )
            else:
                # Bounded AI pass over a sample; deterministic results stay ground truth.
                from app.services.ai_assist import extract_obligations_ai
                from app.services.obligations import persist_ai_obligations

                sample = "\n\n".join(c.text for c in document.chunks[:3])[:12000] if hasattr(document, "chunks") else ""
                ai = extract_obligations_ai(sample or document.filename, document.filename)
                warnings.extend(ai["warnings"])
                if ai["ok"] and ai["parsed"] is not None:
                    method = ai["provider"]
                    execution_mode = ai["execution_mode"]
                    provider_used = ai["provider"]
                    model_used = ai["model"]
                    try:
                        ai_created, ai_warnings = persist_ai_obligations(
                            db,
                            list(ai["parsed"].obligations or []),
                            regulation_version=version,
                            document=document,
                            provider=ai["provider"],
                            model=ai["model"],
                            source_text=sample,
                        )
                        warnings.extend(ai_warnings)
                        warnings.append(
                            f"AI-assisted extraction ({ai['provider']}/{ai['model']}) validated "
                            f"against the obligation schema: {len(ai_created)} candidate(s) added. "
                            "All still require human approval."
                        )
                    except Exception as exc:  # noqa: BLE001 - honesty boundary
                        ai_created = []
                        warnings.append(f"AI candidates were rejected ({str(exc)[:160]}). Deterministic output stands.")
                else:
                    execution_mode = "deterministic_fallback"
                    warnings.append(
                        "AI-assisted extraction failed; deterministic extraction was used instead. "
                        "No model output is claimed."
                    )

    det_created = persist_obligations(
        db,
        regulation_version=version,
        document=document,
        extraction_method="deterministic",
        extraction_model=None,
        limit=payload.limit,
        chunk_ids=payload.chunk_ids,
    )
    created = [*ai_created, *det_created]
    if not ai_created:
        # No AI rows: never mislabel deterministic output as AI-generated.
        method = "deterministic"
        if execution_mode in ("gemini", "cerebras", "cache"):
            execution_mode = "deterministic"
    record_audit(
        db,
        actor=ctx.actor,
        action="obligations.extracted",
        entity_type="obligation",
        correlation_id=ctx.correlation_id,
        after={
            "count": len(created),
            "method": method,
            "model": model_used,
            "document_id": document.id,
        },
    )
    result = {
        "created": len(created),
        "method": method,
        "execution_mode": execution_mode,
        "provider": provider_used,
        "model": model_used,
        "obligations": [ObligationOut.model_validate(o).model_dump(mode="json") for o in created],
        "warnings": warnings,
    }
    if payload.run_workflow:
        from app.agents import run_workflow

        result["workflow"] = run_workflow(
            db,
            "ingest_and_extract",
            {"regulation_version_id": version.id, "document_id": document.id, "use_llm": payload.use_llm},
            actor=ctx.actor,
        )
    return result


@router.get("/obligations", response_model=dict)
def list_obligations(
    db: Session = Depends(get_db),
    ctx: RequestContext = Depends(get_context),
    regulation_id: str | None = None,
    review_status: str | None = None,
    severity: str | None = None,
    jurisdiction: str | None = None,
    search: str | None = None,
    is_synthetic: bool | None = None,
    page: int = 1,
    page_size: int = 25,
    sort: str = "created_at",
    order: str = "desc",
) -> dict[str, Any]:
    stmt = select(Obligation)
    if regulation_id:
        stmt = stmt.where(Obligation.regulation_id == regulation_id)
    if review_status:
        stmt = stmt.where(Obligation.review_status == review_status)
    if severity:
        stmt = stmt.where(Obligation.severity == severity)
    if jurisdiction:
        stmt = stmt.where(Obligation.jurisdiction == jurisdiction)
    if is_synthetic is not None:
        stmt = stmt.where(Obligation.is_synthetic.is_(is_synthetic))
    if search:
        stmt = stmt.where(Obligation.obligation_text.ilike(f"%{search}%"))
    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    column = getattr(Obligation, sort, Obligation.created_at)
    stmt = stmt.order_by(column.desc() if order == "desc" else column.asc())
    stmt = stmt.offset((page - 1) * page_size).limit(page_size)
    items = [ObligationOut.model_validate(o).model_dump(mode="json") for o in db.scalars(stmt)]
    return page_response(items, total, _params(page, page_size))


@router.get("/obligations/{obligation_id}", response_model=ObligationDetail)
def get_obligation(
    obligation_id: str, db: Session = Depends(get_db), ctx: RequestContext = Depends(get_context)
) -> ObligationDetail:
    obligation = db.get(Obligation, obligation_id)
    if obligation is None:
        raise NotFoundError(f"Obligation {obligation_id} not found")
    from app.models.controls import ObligationControlMapping

    mappings = [
        MappingOut.model_validate(m)
        for m in db.scalars(
            select(ObligationControlMapping).where(ObligationControlMapping.obligation_id == obligation.id)
        )
    ]
    from app.models.obligations import ApplicabilityAssessment

    assessments = [
        {
            "applicable": a.applicable,
            "rationale": a.rationale,
            "assessed_by": a.assessed_by,
            "created_at": a.created_at.isoformat() if a.created_at else None,
        }
        for a in db.scalars(
            select(ApplicabilityAssessment).where(ApplicabilityAssessment.obligation_id == obligation.id)
        )
    ]
    detail = ObligationDetail.model_validate(obligation)
    detail.mappings = mappings
    detail.applicability_assessments = assessments
    return detail


@router.post("/obligations/{obligation_id}/approve", response_model=ObligationOut)
def approve_obligation(
    obligation_id: str,
    payload: ObligationApproveRequest,
    db: Session = Depends(get_db),
    ctx: RequestContext = Depends(get_context),
) -> ObligationOut:
    from datetime import datetime, timezone

    from app.services.audit import record_approval

    obligation = db.get(Obligation, obligation_id)
    if obligation is None:
        raise NotFoundError(f"Obligation {obligation_id} not found")
    before = obligation.review_status
    obligation.review_status = ReviewStatus(payload.decision)
    obligation.reviewed_by = payload.reviewer
    obligation.reviewed_at = datetime.now(timezone.utc)
    obligation.review_notes = payload.notes
    record_approval(
        db,
        entity_type="obligation",
        entity_id=obligation.id,
        decision=payload.decision,
        decided_by=payload.reviewer,
        reason=payload.notes,
        before_status=before,
        after_status=payload.decision,
    )
    record_audit(
        db,
        actor=payload.reviewer,
        actor_role=ctx.actor_role,
        action="obligation.reviewed",
        entity_type="obligation",
        entity_id=obligation.id,
        entity_uid=obligation.obligation_uid,
        correlation_id=ctx.correlation_id,
        before={"review_status": str(before)},
        after={"review_status": payload.decision, "notes": payload.notes},
        request_path=ctx.path,
        request_method=ctx.method,
        ip_address=ctx.ip,
    )
    return ObligationOut.model_validate(obligation)


__all__ = ["router"]
