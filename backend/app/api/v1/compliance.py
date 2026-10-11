"""Controls, mappings, evidence, tests, gaps, risk and remediation endpoints."""
from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import RequestContext, get_context, get_db, page_response
from app.errors import NotFoundError, ValidationError
from app.logging_config import get_logger
from app.models.core import (
    EvidenceValidity,
    GapStatus,
    MappingStatus,
    RemediationStatus,
    ReviewStatus,
    TestResult,
    enum_value,
)
from app.models.controls import Control, ObligationControlMapping
from app.models.evidence import ControlTest, Evidence, EvidenceControlLink
from app.models.obligations import Obligation
from app.models.remediation import (
    ComplianceGap,
    RemediationAction,
    RiskAssessment,
)
from app.schemas import (
    AssessmentRunRequest,
    ControlCreate,
    ControlDetail,
    ControlOut,
    ControlTestOut,
    EvidenceCreate,
    EvidenceLinkRequest,
    EvidenceOut,
    GapDetail,
    GapOut,
    MappingCandidateOut,
    MappingDecisionRequest,
    MappingOut,
    RemediationActionOut,
    RemediationCompleteRequest,
    RemediationDecisionRequest,
    RemediationGenerateRequest,
    RiskAssessmentOut,
    RiskRecalculateRequest,
    RiskSummary,
    RunTestsRequest,
    RunTestsResponse,
    SuggestMappingsRequest,
    SuggestMappingsResponse,
)
from app.services.audit import record_approval, record_audit
from app.services.evidence import evidence_state_for_control

router = APIRouter()
log = get_logger("regulens.api.compliance")


def _params(page: int, page_size: int):
    from app.schemas import PageParams

    return PageParams(page=page, page_size=page_size)


# ---------------------------------------------------------------------------
# Controls
# ---------------------------------------------------------------------------
@router.post("/controls", response_model=ControlOut, status_code=201)
def create_control(
    payload: ControlCreate, db: Session = Depends(get_db), ctx: RequestContext = Depends(get_context)
) -> ControlOut:
    import secrets

    from app.services.assessment import assess_design

    uid = payload.control_uid or f"CTL-{secrets.token_hex(4).upper()}"
    control = Control(**payload.model_dump(exclude={"control_uid"}), control_uid=uid, status=ReviewStatus.PENDING)
    design = assess_design(control)
    control.design_effectiveness = design.status
    control.design_rationale = design.rationale
    db.add(control)
    db.flush()
    record_audit(
        db,
        actor=ctx.actor,
        action="control.created",
        entity_type="control",
        entity_id=control.id,
        entity_uid=control.control_uid,
        correlation_id=ctx.correlation_id,
        after=payload.model_dump(mode="json"),
    )
    return ControlOut.model_validate(control)


@router.get("/controls", response_model=dict)
def list_controls(
    db: Session = Depends(get_db),
    ctx: RequestContext = Depends(get_context),
    control_type: str | None = None,
    status: str | None = None,
    business_unit_id: str | None = None,
    operating_effectiveness: str | None = None,
    search: str | None = None,
    page: int = 1,
    page_size: int = 25,
    sort: str = "created_at",
    order: str = "desc",
) -> dict[str, Any]:
    stmt = select(Control)
    if control_type:
        stmt = stmt.where(Control.control_type == control_type)
    if status:
        stmt = stmt.where(Control.status == status)
    if business_unit_id:
        stmt = stmt.where(Control.business_unit_id == business_unit_id)
    if operating_effectiveness:
        stmt = stmt.where(Control.operating_effectiveness == operating_effectiveness)
    if search:
        stmt = stmt.where(Control.title.ilike(f"%{search}%"))
    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    column = getattr(Control, sort, Control.created_at)
    stmt = stmt.order_by(column.desc() if order == "desc" else column.asc())
    stmt = stmt.offset((page - 1) * page_size).limit(page_size)
    items = [ControlOut.model_validate(c).model_dump(mode="json") for c in db.scalars(stmt)]
    return page_response(items, total, _params(page, page_size))


@router.get("/controls/{control_id}", response_model=ControlDetail)
def get_control(
    control_id: str, db: Session = Depends(get_db), ctx: RequestContext = Depends(get_context)
) -> ControlDetail:
    control = db.get(Control, control_id)
    if control is None:
        raise NotFoundError(f"Control {control_id} not found")
    links = list(
        db.scalars(select(EvidenceControlLink).where(EvidenceControlLink.control_id == control.id))
    )
    evidence = [
        EvidenceOut.model_validate(db.get(Evidence, link.evidence_id))
        for link in links
        if db.get(Evidence, link.evidence_id)
    ]
    tests = [
        ControlTestOut.model_validate(t)
        for t in db.scalars(select(ControlTest).where(ControlTest.control_id == control.id))
    ]
    mappings = [
        MappingOut.model_validate(m)
        for m in db.scalars(
            select(ObligationControlMapping).where(ObligationControlMapping.control_id == control.id)
        )
    ]
    detail = ControlDetail.model_validate(control)
    detail.evidence = evidence
    detail.tests = tests
    detail.mappings = mappings
    detail.evidence_summary = evidence_state_for_control(db, control.id) or {}
    return detail


# ---------------------------------------------------------------------------
# Mappings
# ---------------------------------------------------------------------------
@router.post("/mappings/suggest", response_model=SuggestMappingsResponse)
def suggest_mappings(
    payload: SuggestMappingsRequest,
    db: Session = Depends(get_db),
    ctx: RequestContext = Depends(get_context),
) -> SuggestMappingsResponse:
    from app.services.mapping import persist_candidates, suggest_mappings as _suggest

    stmt = select(Obligation).where(Obligation.review_status == ReviewStatus.APPROVED)
    if payload.obligation_ids:
        stmt = stmt.where(Obligation.id.in_(payload.obligation_ids))
    obligations = list(db.scalars(stmt))
    results: dict[str, list[MappingCandidateOut]] = {}
    persisted = 0
    warnings: list[str] = [
        "Candidates are not approved mappings. Approve each mapping explicitly before relying on it."
    ]
    execution_mode = "deterministic"
    provider_used: str | None = None
    for obligation in obligations:
        candidates = _suggest(
            db,
            obligation,
            use_vectors=payload.use_vectors,
            business_unit_id=payload.business_unit_id,
        )
        if payload.use_ai:
            from app.config import settings as _settings
            from app.llm.router import get_router as _get_router
            from app.services.ai_assist import propose_mapping_ai
            from app.services.mapping import MappingCandidate as _MC

            if _settings.effective_deterministic:
                warnings.append(
                    f"use_ai=true for {obligation.obligation_uid} but no hosted provider is "
                    "available — deterministic candidates only; no model output claimed."
                )
                execution_mode = "deterministic_fallback" if execution_mode == "deterministic" else execution_mode
            else:
                _router = _get_router()
                _controls_by_uid = {}
                _ctrl_texts: list[dict[str, Any]] = []
                for _c in candidates[:10]:
                    _ctrl = db.get(Control, _c.control_id)
                    if _ctrl is None:
                        continue
                    _controls_by_uid[_ctrl.control_uid] = _ctrl
                    _ctrl_texts.append({"control_uid": _ctrl.control_uid, "title": _ctrl.title,
                                        "objective": _ctrl.control_objective, "activity": _ctrl.control_activity})
                if _ctrl_texts:
                    _ai = propose_mapping_ai(obligation.obligation_text, _ctrl_texts)
                    warnings.extend(_ai["warnings"])
                    if _ai["ok"] and _ai["parsed"] is not None:
                        execution_mode = _ai["execution_mode"]
                        provider_used = _ai["provider"]
                        for _item in _ai["parsed"].mappings or []:
                            _ctrl = _controls_by_uid.get(_item.control_uid)
                            if _ctrl is None:
                                warnings.append(
                                    f"Rejected an AI mapping to unknown control '{_item.control_uid}'."
                                )
                                continue
                            candidates.append(_MC(
                                control_id=_ctrl.id,
                                confidence=round(min(0.95, float(_item.confidence)), 3),
                                rationale=f"[AI { _ai['provider']}] {_item.rationale}",
                                lexical_score=0.0,
                                vector_score=0.0,
                                shared_terms=[],
                                proposed_by=f"ai_{_ai['provider']}",
                                proposed_model=_ai["model"],
                            ))
                    else:
                        execution_mode = "deterministic_fallback"
        results[obligation.id] = [MappingCandidateOut(**vars(c)) for c in candidates]
        if payload.persist:
            persisted += len(persist_candidates(db, obligation, candidates, actor=payload.actor))
    record_audit(
        db,
        actor=payload.actor,
        action="mappings.suggested",
        entity_type="obligation_control_mapping",
        correlation_id=ctx.correlation_id,
        after={"obligations": len(obligations), "persisted": persisted, "use_vectors": payload.use_vectors,
               "use_ai": payload.use_ai, "execution_mode": execution_mode},
    )
    return SuggestMappingsResponse(
        results=results,
        persisted=persisted,
        warnings=warnings,
        execution_mode=execution_mode,
        provider=provider_used,
    )


@router.get("/mappings", response_model=dict)
def list_mappings(
    db: Session = Depends(get_db),
    ctx: RequestContext = Depends(get_context),
    status: str | None = None,
    obligation_id: str | None = None,
    control_id: str | None = None,
    page: int = 1,
    page_size: int = 25,
    sort: str = "created_at",
    order: str = "desc",
) -> dict[str, Any]:
    stmt = select(ObligationControlMapping)
    if status:
        stmt = stmt.where(ObligationControlMapping.status == status)
    if obligation_id:
        stmt = stmt.where(ObligationControlMapping.obligation_id == obligation_id)
    if control_id:
        stmt = stmt.where(ObligationControlMapping.control_id == control_id)
    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    column = getattr(ObligationControlMapping, sort, ObligationControlMapping.created_at)
    stmt = stmt.order_by(column.desc() if order == "desc" else column.asc())
    stmt = stmt.offset((page - 1) * page_size).limit(page_size)
    items = [MappingOut.model_validate(m).model_dump(mode="json") for m in db.scalars(stmt)]
    return page_response(items, total, _params(page, page_size))


@router.post("/mappings/{mapping_id}/approve", response_model=MappingOut)
def decide_mapping(
    mapping_id: str,
    payload: MappingDecisionRequest,
    db: Session = Depends(get_db),
    ctx: RequestContext = Depends(get_context),
) -> MappingOut:
    mapping = db.get(ObligationControlMapping, mapping_id)
    if mapping is None:
        raise NotFoundError(f"Mapping {mapping_id} not found")
    before = mapping.status
    mapping.status = MappingStatus(payload.decision)
    mapping.reviewer_decision = payload.decision
    mapping.reviewed_by = payload.reviewer
    mapping.reviewed_at = datetime.now(timezone.utc)
    mapping.reject_reason = payload.reason if payload.decision == "rejected" else None
    record_approval(
        db,
        entity_type="obligation_control_mapping",
        entity_id=mapping.id,
        decision=payload.decision,
        decided_by=payload.reviewer,
        reason=payload.reason,
        before_status=str(before),
        after_status=payload.decision,
    )
    record_audit(
        db,
        actor=payload.reviewer,
        action="mapping.reviewed",
        entity_type="obligation_control_mapping",
        entity_id=mapping.id,
        correlation_id=ctx.correlation_id,
        before={"status": str(before)},
        after={"status": payload.decision, "reason": payload.reason},
        request_path=ctx.path,
        request_method=ctx.method,
        ip_address=ctx.ip,
    )
    return MappingOut.model_validate(mapping)


# ---------------------------------------------------------------------------
# Evidence
# ---------------------------------------------------------------------------
@router.post("/evidence", response_model=EvidenceOut, status_code=201)
def create_evidence(
    payload: EvidenceCreate, db: Session = Depends(get_db), ctx: RequestContext = Depends(get_context)
) -> EvidenceOut:
    import secrets

    from app.services.assessment import assess_evidence

    evidence = Evidence(
        evidence_uid=f"EVD-{secrets.token_hex(4).upper()}",
        title=payload.title,
        description=payload.description,
        evidence_period_start=payload.evidence_period_start,
        evidence_period_end=payload.evidence_period_end,
        source=payload.source,
        collection_date=payload.collection_date,
        file_id=payload.file_id,
        is_synthetic=payload.is_synthetic,
        uploaded_by=payload.uploaded_by,
        review_status=ReviewStatus.PENDING,
    )
    db.add(evidence)
    db.flush()
    for control_id in payload.control_ids:
        db.add(
            EvidenceControlLink(
                evidence_id=evidence.id, control_id=control_id, created_by=payload.uploaded_by
            )
        )
    db.flush()
    result = assess_evidence(evidence)
    evidence.validity_status = result.validity
    evidence.validity_rationale = result.rationale
    evidence.freshness_status = result.freshness
    record_audit(
        db,
        actor=payload.uploaded_by,
        action="evidence.created",
        entity_type="evidence",
        entity_id=evidence.id,
        entity_uid=evidence.evidence_uid,
        correlation_id=ctx.correlation_id,
        after={"controls": payload.control_ids, "period_end": str(payload.evidence_period_end)},
    )
    return EvidenceOut.model_validate(evidence)


@router.get("/evidence", response_model=dict)
def list_evidence(
    db: Session = Depends(get_db),
    ctx: RequestContext = Depends(get_context),
    validity_status: str | None = None,
    review_status: str | None = None,
    control_id: str | None = None,
    page: int = 1,
    page_size: int = 25,
    sort: str = "created_at",
    order: str = "desc",
) -> dict[str, Any]:
    stmt = select(Evidence)
    if validity_status:
        stmt = stmt.where(Evidence.validity_status == validity_status)
    if review_status:
        stmt = stmt.where(Evidence.review_status == review_status)
    if control_id:
        link_ids = [
            l.evidence_id
            for l in db.scalars(select(EvidenceControlLink).where(EvidenceControlLink.control_id == control_id))
        ]
        stmt = stmt.where(Evidence.id.in_(link_ids or ["__none__"]))
    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    column = getattr(Evidence, sort, Evidence.created_at)
    stmt = stmt.order_by(column.desc() if order == "desc" else column.asc())
    stmt = stmt.offset((page - 1) * page_size).limit(page_size)
    items = [EvidenceOut.model_validate(e).model_dump(mode="json") for e in db.scalars(stmt)]
    return page_response(items, total, _params(page, page_size))


@router.post("/evidence/{evidence_id}/link", response_model=EvidenceOut)
def link_evidence(
    evidence_id: str,
    payload: EvidenceLinkRequest,
    db: Session = Depends(get_db),
    ctx: RequestContext = Depends(get_context),
) -> EvidenceOut:
    evidence = db.get(Evidence, evidence_id)
    if evidence is None:
        raise NotFoundError(f"Evidence {evidence_id} not found")
    for control_id in payload.control_ids:
        exists = db.scalar(
            select(EvidenceControlLink).where(
                EvidenceControlLink.evidence_id == evidence_id,
                EvidenceControlLink.control_id == control_id,
            ).limit(1)
        )
        if exists:
            continue
        db.add(
            EvidenceControlLink(
                evidence_id=evidence_id,
                control_id=control_id,
                link_type=payload.link_type,
                notes=payload.notes,
                created_by=payload.actor,
            )
        )
    db.flush()
    record_audit(
        db,
        actor=payload.actor,
        action="evidence.linked",
        entity_type="evidence",
        entity_id=evidence.id,
        entity_uid=evidence.evidence_uid,
        correlation_id=ctx.correlation_id,
        after={"control_ids": payload.control_ids, "link_type": payload.link_type},
    )
    return EvidenceOut.model_validate(evidence)


@router.post("/evidence/{evidence_id}/review", response_model=EvidenceOut)
def review_evidence(
    evidence_id: str,
    decision: str = Query(..., pattern="^(approved|rejected|needs_revision)$"),
    reviewer: str = Query(...),
    notes: str | None = Query(None),
    db: Session = Depends(get_db),
    ctx: RequestContext = Depends(get_context),
) -> EvidenceOut:
    from app.services.assessment import assess_evidence

    evidence = db.get(Evidence, evidence_id)
    if evidence is None:
        raise NotFoundError(f"Evidence {evidence_id} not found")
    before = evidence.review_status
    evidence.review_status = ReviewStatus(decision)
    if decision == "approved":
        evidence.validity_status = EvidenceValidity.SUFFICIENT
        evidence.validity_rationale = (
            "Marked sufficient by a human reviewer: "
            + (notes or "no additional rationale recorded")
        )
    elif decision == "rejected":
        evidence.validity_status = EvidenceValidity.INSUFFICIENT
        evidence.validity_rationale = notes or "Rejected by a human reviewer."
    evidence.test_result_note = notes
    record_approval(
        db,
        entity_type="evidence",
        entity_id=evidence.id,
        decision=decision,
        decided_by=reviewer,
        reason=notes,
        before_status=str(before),
        after_status=decision,
    )
    record_audit(
        db,
        actor=reviewer,
        action="evidence.reviewed",
        entity_type="evidence",
        entity_id=evidence.id,
        entity_uid=evidence.evidence_uid,
        correlation_id=ctx.correlation_id,
        before={"review_status": str(before)},
        after={"review_status": decision, "notes": notes},
        request_path=ctx.path,
        request_method=ctx.method,
        ip_address=ctx.ip,
    )
    return EvidenceOut.model_validate(evidence)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------
@router.post("/tests/run", response_model=RunTestsResponse)
def run_tests(
    payload: RunTestsRequest,
    db: Session = Depends(get_db),
    ctx: RequestContext = Depends(get_context),
) -> RunTestsResponse:
    from app.services.assessment import run_design_test, run_operating_test

    stmt = select(Control)
    if payload.control_ids:
        stmt = stmt.where(Control.id.in_(payload.control_ids))
    controls = list(db.scalars(stmt))
    tests: list[ControlTest] = []
    results: dict[str, int] = {}
    for control in controls:
        if payload.test_type in ("design", "both"):
            tests.append(run_design_test(db, control, actor=payload.actor))
        if payload.test_type in ("operating", "both"):
            tests.append(run_operating_test(db, control, actor=payload.actor))
    for test in tests:
        key = str(test.result)
        results[key] = results.get(key, 0) + 1
    record_audit(
        db,
        actor=payload.actor,
        action="tests.executed",
        entity_type="control_test",
        correlation_id=ctx.correlation_id,
        after={"tests": len(tests), "results": results, "type": payload.test_type},
    )
    return RunTestsResponse(
        tests_run=len(tests),
        results=results,
        tests=[ControlTestOut.model_validate(t) for t in tests],
    )


@router.get("/tests/results", response_model=dict)
def list_test_results(
    db: Session = Depends(get_db),
    ctx: RequestContext = Depends(get_context),
    result: str | None = None,
    control_id: str | None = None,
    page: int = 1,
    page_size: int = 25,
    sort: str = "created_at",
    order: str = "desc",
) -> dict[str, Any]:
    stmt = select(ControlTest)
    if result:
        stmt = stmt.where(ControlTest.result == result)
    if control_id:
        stmt = stmt.where(ControlTest.control_id == control_id)
    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    column = getattr(ControlTest, sort, ControlTest.created_at)
    stmt = stmt.order_by(column.desc() if order == "desc" else column.asc())
    stmt = stmt.offset((page - 1) * page_size).limit(page_size)
    items = [ControlTestOut.model_validate(t).model_dump(mode="json") for t in db.scalars(stmt)]
    return page_response(items, total, _params(page, page_size))


# ---------------------------------------------------------------------------
# Assessment / gaps
# ---------------------------------------------------------------------------
@router.post("/assessments/run", response_model=dict)
def run_assessment(
    payload: AssessmentRunRequest,
    db: Session = Depends(get_db),
    ctx: RequestContext = Depends(get_context),
) -> dict[str, Any]:
    from app.services.gaps import detect_gaps

    created = detect_gaps(db, obligation_ids=payload.obligation_ids)
    by_type: dict[str, int] = {}
    for gap in created:
        by_type[enum_value(gap.gap_type)] = by_type.get(enum_value(gap.gap_type), 0) + 1
    record_audit(
        db,
        actor=payload.actor,
        action="assessment.gaps_detected",
        entity_type="compliance_gap",
        correlation_id=ctx.correlation_id,
        after={"created": len(created), "by_type": by_type},
    )
    out = {
        "gaps_created": len(created),
        "by_type": by_type,
        "gaps": [GapOut.model_validate(g).model_dump(mode="json") for g in created],
    }
    if payload.run_workflow:
        from app.agents import run_workflow

        out["workflow"] = run_workflow(db, "compliance_assessment", {}, actor=payload.actor)
    return out


@router.get("/assessments", response_model=dict)
def list_assessments(
    db: Session = Depends(get_db),
    ctx: RequestContext = Depends(get_context),
    control_id: str | None = None,
    page: int = 1,
    page_size: int = 25,
    sort: str = "calculated_at",
    order: str = "desc",
) -> dict[str, Any]:
    stmt = select(RiskAssessment)
    if control_id:
        gap_ids = [
            g.id
            for g in db.scalars(select(ComplianceGap).where(ComplianceGap.control_id == control_id))
        ]
        stmt = stmt.where(RiskAssessment.gap_id.in_(gap_ids or ["__none__"]))
    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    column = getattr(RiskAssessment, sort, RiskAssessment.calculated_at)
    stmt = stmt.order_by(column.desc() if order == "desc" else column.asc())
    stmt = stmt.offset((page - 1) * page_size).limit(page_size)
    items = [RiskAssessmentOut.model_validate(r).model_dump(mode="json") for r in db.scalars(stmt)]
    return page_response(items, total, _params(page, page_size))


@router.get("/gaps", response_model=dict)
def list_gaps(
    db: Session = Depends(get_db),
    ctx: RequestContext = Depends(get_context),
    status: str | None = None,
    gap_type: str | None = None,
    jurisdiction: str | None = None,
    business_unit_id: str | None = None,
    band: str | None = None,
    search: str | None = None,
    page: int = 1,
    page_size: int = 25,
    sort: str = "created_at",
    order: str = "desc",
) -> dict[str, Any]:
    stmt = select(ComplianceGap)
    if status:
        stmt = stmt.where(ComplianceGap.status == status)
    if gap_type:
        stmt = stmt.where(ComplianceGap.gap_type == gap_type)
    if business_unit_id:
        stmt = stmt.where(ComplianceGap.business_unit_id == business_unit_id)
    if jurisdiction:
        gap_ids = [
            o.id
            for o in db.scalars(select(Obligation).where(Obligation.jurisdiction == jurisdiction))
        ]
        stmt = stmt.where(ComplianceGap.obligation_id.in_(gap_ids or ["__none__"]))
    if band:
        gap_ids = [
            r.gap_id
            for r in db.scalars(select(RiskAssessment).where(RiskAssessment.score_band == band))
        ]
        stmt = stmt.where(ComplianceGap.id.in_(gap_ids or ["__none__"]))
    if search:
        stmt = stmt.where(ComplianceGap.title.ilike(f"%{search}%"))
    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    column = getattr(ComplianceGap, sort, ComplianceGap.created_at)
    stmt = stmt.order_by(column.desc() if order == "desc" else column.asc())
    stmt = stmt.offset((page - 1) * page_size).limit(page_size)
    items = [GapOut.model_validate(g).model_dump(mode="json") for g in db.scalars(stmt)]
    return page_response(items, total, _params(page, page_size))


@router.get("/gaps/{gap_id}", response_model=GapDetail)
def get_gap(
    gap_id: str, db: Session = Depends(get_db), ctx: RequestContext = Depends(get_context)
) -> GapDetail:
    gap = db.get(ComplianceGap, gap_id)
    if gap is None:
        raise NotFoundError(f"Gap {gap_id} not found")
    detail = GapDetail.model_validate(gap)
    detail.risk_assessments = [
        RiskAssessmentOut.model_validate(r) for r in gap.risk_assessments
    ]
    detail.remediation_actions = [
        RemediationActionOut.model_validate(a) for a in gap.remediation_actions
    ]
    detail.detection_context = _decode_json(gap.detection_context)
    detail.traceability = _build_traceability(db, gap)
    return detail


def _decode_json(value: Any) -> Any:
    if isinstance(value, str):
        import json

        try:
            return json.loads(value)
        except (TypeError, ValueError):
            return {}
    return value or {}


def _build_traceability(db: Session, gap: ComplianceGap) -> dict[str, Any]:
    obligation = db.get(Obligation, gap.obligation_id) if gap.obligation_id else None
    control = db.get(Control, gap.control_id) if gap.control_id else None
    changes: list[dict[str, Any]] = []
    if obligation is not None:
        from app.models.obligations import ImpactAssessment, RegulatoryChange

        impacts = list(db.scalars(select(ImpactAssessment).where(ImpactAssessment.status != "superseded")))
        for impact in impacts:
            ids = _decode_json(impact.affected_obligation_ids)
            if isinstance(ids, list) and obligation.id in ids:
                change = db.get(RegulatoryChange, impact.change_id)
                changes.append(
                    {
                        "impact_uid": impact.impact_uid,
                        "change_uid": change.change_uid if change else None,
                        "change_type": str(change.change_type) if change else None,
                        "summary": change.summary if change else None,
                        "impact_score": impact.impact_score,
                    }
                )
    return {
        "gap_uid": gap.gap_uid,
        "obligation": (
            {
                "uid": obligation.obligation_uid,
                "text": obligation.obligation_text,
                "quoted_text": obligation.quoted_text,
                "page": obligation.source_page,
                "section": obligation.source_section,
                "document_id": obligation.source_document_id,
                "review_status": str(obligation.review_status),
            }
            if obligation
            else None
        ),
        "control": (
            {
                "uid": control.control_uid,
                "title": control.title,
                "design_effectiveness": str(control.design_effectiveness),
                "operating_effectiveness": str(control.operating_effectiveness),
            }
            if control
            else None
        ),
        "regulatory_changes": changes,
        "risk": (
            {
                "score": gap.risk_assessments[-1].score,
                "band": gap.risk_assessments[-1].score_band,
            }
            if gap.risk_assessments
            else None
        ),
    }


@router.post("/gaps/{gap_id}/accept-risk", response_model=GapOut)
def accept_gap_risk(
    gap_id: str,
    reason: str = Query(..., min_length=5),
    approver: str = Query(...),
    db: Session = Depends(get_db),
    ctx: RequestContext = Depends(get_context),
) -> GapOut:
    gap = db.get(ComplianceGap, gap_id)
    if gap is None:
        raise NotFoundError(f"Gap {gap_id} not found")
    gap.status = GapStatus.ACCEPTED_RISK
    gap.accepted_risk_reason = reason
    gap.approved_by = approver
    record_audit(
        db,
        actor=approver,
        action="gap.risk_accepted",
        entity_type="compliance_gap",
        entity_id=gap.id,
        entity_uid=gap.gap_uid,
        correlation_id=ctx.correlation_id,
        after={"status": str(gap.status), "reason": reason},
    )
    return GapOut.model_validate(gap)


# ---------------------------------------------------------------------------
# Risk
# ---------------------------------------------------------------------------
@router.post("/risks/recalculate", response_model=dict)
def recalculate_risk(
    payload: RiskRecalculateRequest,
    db: Session = Depends(get_db),
    ctx: RequestContext = Depends(get_context),
) -> dict[str, Any]:
    from app.services.risk import normalise_weights, score_gap

    weights = None
    if payload.weights:
        try:
            weights = normalise_weights(payload.weights)
        except ValueError as exc:
            raise ValidationError(str(exc)) from exc
    stmt = select(ComplianceGap).where(ComplianceGap.status.in_([GapStatus.OPEN, GapStatus.IN_REMEDIATION]))
    if payload.gap_ids:
        stmt = stmt.where(ComplianceGap.id.in_(payload.gap_ids))
    scored: list[dict[str, Any]] = []
    for gap in db.scalars(stmt):
        assessment, explanation = score_gap(db, gap, weights=weights, actor=payload.actor)
        scored.append(
            {
                "gap_uid": gap.gap_uid,
                "score": assessment.score,
                "band": assessment.score_band,
                "hard_constraint": assessment.hard_constraint_flag,
                "narrative": explanation.narrative,
            }
        )
    record_audit(
        db,
        actor=payload.actor,
        action="risk.recalculated",
        entity_type="risk_assessment",
        correlation_id=ctx.correlation_id,
        after={"scored": len(scored), "weights": weights},
    )
    return {"scored": len(scored), "assessments": scored, "weights": weights}


@router.get("/risks/summary", response_model=RiskSummary)
def risk_summary(
    db: Session = Depends(get_db), ctx: RequestContext = Depends(get_context)
) -> RiskSummary:
    from app.config import settings
    from app.services.risk import risk_summary as _summary

    summary = _summary(db)
    return RiskSummary(
        total_assessed=summary["total_assessed"],
        bands=summary["bands"],
        max_score=summary["max_score"],
        mean_score=summary["mean_score"],
        weights_in_use={k: float(v) for k, v in (settings.RISK_WEIGHTS or {}).items()},
        calibrated=False,
    )


# ---------------------------------------------------------------------------
# Remediation
# ---------------------------------------------------------------------------
@router.post("/remediation/generate", response_model=dict)
def generate_remediation(
    payload: RemediationGenerateRequest,
    db: Session = Depends(get_db),
    ctx: RequestContext = Depends(get_context),
) -> dict[str, Any]:
    from app.services.remediation import generate_for_gaps

    created = generate_for_gaps(db, gap_ids=payload.gap_ids)
    ai_notes: list[str] = []
    execution_mode = "deterministic"
    provider_used: str | None = None
    if payload.use_ai and created:
        from app.config import settings as _settings
        from app.services.ai_assist import propose_remediation_ai

        if _settings.effective_deterministic:
            ai_notes.append(
                "use_ai=true but no hosted provider is available — template recommendations only; "
                "no model output claimed."
            )
            execution_mode = "deterministic_fallback"
        else:
            _summaries = [
                {"gap_id": a.gap_id,
                 "action": (a.proposed_action or "")[:500], "reason": (a.reason or "")[:500]}
                for a in created[:8]
            ]
            _ai = propose_remediation_ai(_summaries)
            ai_notes.extend(_ai["warnings"])
            if _ai["ok"] and _ai["parsed"] is not None:
                execution_mode = _ai["execution_mode"]
                provider_used = _ai["provider"]
                for _action, _item in zip(created, (_ai["parsed"].remediations or [])):
                    _action.uncertainty_notes = "; ".join(
                        x for x in [
                            _action.uncertainty_notes,
                            f"AI draft ({_ai['provider']}, requires approval): {_item.proposed_action[:500]}",
                            *([f"AI warning: {w}" for w in (_item.warnings or [])]),
                        ] if x
                    ) or None
                ai_notes.append(
                    f"AI drafts ({_ai['provider']}/{_ai['model']}) attached as labelled, "
                    "approval-required notes. Template cost/duration unchanged (never AI-invented)."
                )
            else:
                execution_mode = "deterministic_fallback"
    record_audit(
        db,
        actor=payload.actor,
        action="remediation.generated",
        entity_type="remediation_action",
        correlation_id=ctx.correlation_id,
        after={"actions": len(created), "use_ai": payload.use_ai, "execution_mode": execution_mode},
    )
    out = {
        "actions_created": len(created),
        "actions": [RemediationActionOut.model_validate(a).model_dump(mode="json") for a in created],
        "unknown_cost": sum(1 for a in created if a.estimated_cost_inr is None),
        "execution_mode": execution_mode,
        "provider": provider_used,
        "warnings": ai_notes,
    }
    if payload.run_workflow:
        from app.agents import run_workflow

        out["workflow"] = run_workflow(db, "compliance_assessment", {}, actor=payload.actor)
    return out


@router.get("/remediation", response_model=dict)
def list_remediation(
    db: Session = Depends(get_db),
    ctx: RequestContext = Depends(get_context),
    status: str | None = None,
    gap_id: str | None = None,
    overdue_only: bool = False,
    page: int = 1,
    page_size: int = 25,
    sort: str = "created_at",
    order: str = "desc",
) -> dict[str, Any]:
    stmt = select(RemediationAction)
    if status:
        stmt = stmt.where(RemediationAction.status == status)
    if gap_id:
        stmt = stmt.where(RemediationAction.gap_id == gap_id)
    if overdue_only:
        today = date.today()
        stmt = stmt.where(
            RemediationAction.deadline.is_not(None),
            RemediationAction.deadline < today,
            RemediationAction.status.in_(
                [RemediationStatus.APPROVED, RemediationStatus.IN_PROGRESS]
            ),
        )
    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    column = getattr(RemediationAction, sort, RemediationAction.created_at)
    stmt = stmt.order_by(column.desc() if order == "desc" else column.asc())
    stmt = stmt.offset((page - 1) * page_size).limit(page_size)
    items = [RemediationActionOut.model_validate(a).model_dump(mode="json") for a in db.scalars(stmt)]
    return page_response(items, total, _params(page, page_size))


@router.post("/remediation/{action_id}/approve", response_model=RemediationActionOut)
def decide_remediation(
    action_id: str,
    payload: RemediationDecisionRequest,
    db: Session = Depends(get_db),
    ctx: RequestContext = Depends(get_context),
) -> RemediationActionOut:
    action = db.get(RemediationAction, action_id)
    if action is None:
        raise NotFoundError(f"Remediation action {action_id} not found")
    before = action.status
    action.status = RemediationStatus(payload.decision)
    if payload.decision == "approved":
        action.approved_by = payload.approver
        action.approved_at = datetime.now(timezone.utc)
        action.rejected_reason = None
    else:
        action.rejected_reason = payload.reason
    record_approval(
        db,
        entity_type="remediation_action",
        entity_id=action.id,
        decision=payload.decision,
        decided_by=payload.approver,
        reason=payload.reason,
        before_status=str(before),
        after_status=str(action.status),
    )
    record_audit(
        db,
        actor=payload.approver,
        action="remediation.reviewed",
        entity_type="remediation_action",
        entity_id=action.id,
        entity_uid=action.action_uid,
        correlation_id=ctx.correlation_id,
        before={"status": str(before)},
        after={"status": str(action.status), "reason": payload.reason},
        request_path=ctx.path,
        request_method=ctx.method,
        ip_address=ctx.ip,
    )
    return RemediationActionOut.model_validate(action)


@router.post("/remediation/{action_id}/complete", response_model=RemediationActionOut)
def complete_remediation(
    action_id: str,
    payload: RemediationCompleteRequest,
    db: Session = Depends(get_db),
    ctx: RequestContext = Depends(get_context),
) -> RemediationActionOut:
    action = db.get(RemediationAction, action_id)
    if action is None:
        raise NotFoundError(f"Remediation action {action_id} not found")
    if action.status != RemediationStatus.APPROVED and action.status != RemediationStatus.IN_PROGRESS:
        raise ValidationError(
            "Only an approved remediation action can be completed. Approve it first."
        )
    if not payload.closure_evidence.strip():
        raise ValidationError("Closure evidence is required to complete a remediation action.")
    action.status = RemediationStatus.COMPLETED
    action.completed_at = datetime.now(timezone.utc)
    action.closure_evidence = payload.closure_evidence

    gap = db.get(ComplianceGap, action.gap_id)
    if gap is not None and payload.gap_status == "closed":
        gap.status = GapStatus.CLOSED
        gap.closed_at = datetime.now(timezone.utc)
        gap.closure_evidence = payload.closure_evidence
        gap.approved_by = payload.completed_by
    record_audit(
        db,
        actor=payload.completed_by,
        action="remediation.completed",
        entity_type="remediation_action",
        entity_id=action.id,
        entity_uid=action.action_uid,
        correlation_id=ctx.correlation_id,
        before={"status": str(RemediationStatus.APPROVED)},
        after={
            "status": str(action.status),
            "closure_evidence": payload.closure_evidence,
            "gap_status": str(gap.status) if gap else None,
        },
        request_path=ctx.path,
        request_method=ctx.method,
        ip_address=ctx.ip,
    )
    return RemediationActionOut.model_validate(action)


__all__ = ["router"]

