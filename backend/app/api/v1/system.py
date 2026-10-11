"""Impact, scenario, analysis, audit, agent and system endpoints."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import RequestContext, get_context, get_db, page_response
from app.errors import NotFoundError, ValidationError
from app.logging_config import get_logger
from app.models.audit import AgentEvent, AgentRun, AuditEvent
from app.models.core import ReviewStatus, enum_value
from app.models.obligations import ImpactAssessment, Obligation, RegulatoryChange
from app.models.regulatory import Document
from app.models.remediation import (
    ComplianceGap,
    RemediationAction,
    RemediationScenario,
    ScenarioResult,
)
from app.schemas import (
    AgentRunOut,
    AuditEventOut,
    ContradictionOut,
    CrossRegulationOut,
    DashboardOut,
    GapOut,
    HealthOut,
    ImpactAnalyzeRequest,
    ImpactOut,
    ScenarioCreate,
    ScenarioOut,
    ScenarioResultOut,
    SystemStatusOut,
    WorkflowRunRequest,
    WorkflowRunResponse,
)
from app.services.audit import record_audit, recent_activity

router = APIRouter()
log = get_logger("regulens.api.system")


def _params(page: int, page_size: int):
    from app.schemas import PageParams

    return PageParams(page=page, page_size=page_size)


# ---------------------------------------------------------------------------
# Health & status
# ---------------------------------------------------------------------------
@router.get("/health", response_model=HealthOut)
def health() -> HealthOut:
    from app.config import settings

    return HealthOut(
        status="ok",
        app=settings.APP_NAME,
        version=settings.APP_VERSION,
        tagline=settings.APP_TAGLINE,
        timestamp=datetime.now(timezone.utc),
    )


@router.get("/system/status", response_model=SystemStatusOut)
def system_status(
    db: Session = Depends(get_db), ctx: RequestContext = Depends(get_context)
) -> SystemStatusOut:
    from app.agents import get_orchestrator
    from app.config import settings
    from app.llm.embeddings import embedding_status
    from app.llm.router import get_router
    from app.parsing.ocr import ocr_status
    from app.services.scenarios import CpsatOptimiser, GreedyOptimiser

    db_info: dict[str, Any] = {"url_scheme": settings.DATABASE_URL.split("://", 1)[0]}
    try:
        from sqlalchemy import text

        db.execute(text("SELECT 1"))
        db_info["connected"] = True
    except Exception as exc:
        db_info["connected"] = False
        db_info["error"] = str(exc)
    from app.db import pgvector_enabled

    db_info["pgvector"] = pgvector_enabled()

    counts = {
        "documents": db.scalar(select(func.count()).select_from(Document)) or 0,
        "obligations": db.scalar(select(func.count()).select_from(Obligation)) or 0,
        "obligations_approved": db.scalar(
            select(func.count()).select_from(Obligation).where(Obligation.review_status == ReviewStatus.APPROVED)
        )
        or 0,
        "gaps": db.scalar(select(func.count()).select_from(ComplianceGap)) or 0,
        "gaps_open": db.scalar(
            select(func.count()).select_from(ComplianceGap).where(ComplianceGap.status == "open")
        )
        or 0,
        "remediation_actions": db.scalar(select(func.count()).select_from(RemediationAction)) or 0,
        "regulatory_changes": db.scalar(select(func.count()).select_from(RegulatoryChange)) or 0,
        "impact_assessments": db.scalar(select(func.count()).select_from(ImpactAssessment)) or 0,
        "scenarios": db.scalar(select(func.count()).select_from(RemediationScenario)) or 0,
        "audit_events": db.scalar(select(func.count()).select_from(AuditEvent)) or 0,
        "agent_runs": db.scalar(select(func.count()).select_from(AgentRun)) or 0,
    }

    llm_status = get_router().provider_status_sync()
    emb_status = embedding_status()
    ocr = ocr_status()
    orchestrator = get_orchestrator(db).status()
    cpsat_available = CpsatOptimiser().available()
    optimiser = {
        "preferred": "ortools_cpsat" if cpsat_available else "deterministic_greedy",
        "ortools_available": cpsat_available,
        "fallback": GreedyOptimiser().name,
        "note": "The greedy fallback always runs; the optimiser choice is reported with every scenario result.",
    }

    mode = str(llm_status.get("mode", "deterministic"))
    gemini = llm_status.get("gemini", {})
    cerebras = llm_status.get("cerebras", {})
    ledger = llm_status.get("cerebras_ledger", {})
    # Backwards-compatible `llm` block (old UI shows data.llm).
    llm_compat = {
        "mode": mode,
        "preferred_provider": llm_status.get("preferred_provider"),
        "fallback_provider": llm_status.get("fallback_provider"),
        "gemini_available": gemini.get("available"),
        "gemini_model": gemini.get("model"),
        "cerebras_available": cerebras.get("available"),
        "cerebras_model": cerebras.get("model"),
        "deterministic": llm_status.get("effective_deterministic"),
        "available": bool(gemini.get("available") or cerebras.get("available")),
    }

    warnings: list[str] = []
    if llm_status.get("effective_deterministic"):
        warnings.append(
            "Deterministic mode is effective (no hosted provider enabled/configured, or "
            "DETERMINISTIC_MODE=true) — the system runs on Python rules and no model output is claimed."
        )
    if cerebras.get("available") is False and (ledger.get("disabled") or "budget" in str(cerebras.get("reason", "")).lower()):
        warnings.append(
            f"Cerebras calls are disabled/limited: {ledger.get('disabled_reason') or cerebras.get('reason')}"
        )
    if gemini.get("available") is False and gemini.get("reason"):
        warnings.append(f"Gemini unavailable: {gemini.get('reason')}")
    if emb_status.get("kind") == "lexical":
        warnings.append(
            "Retrieval vectors are hashed TF-IDF lexical vectors, labelled as such — "
            "not neural semantic embeddings."
        )
    if not ocr.get("available"):
        warnings.append("OCR is unavailable — scanned PDFs without a text layer will not yield text.")
    if not db_info.get("pgvector"):
        warnings.append(
            "pgvector extension not present — embeddings are stored "
            "portably and ranked with exact cosine in Python."
        )

    return SystemStatusOut(
        app=settings.APP_NAME,
        version=settings.APP_VERSION,
        environment=settings.ENVIRONMENT,
        mode=mode,
        database=db_info,
        llm=llm_compat,
        embeddings=emb_status,
        ocr=ocr,
        retrieval={
            "lexical": True,
            "lexical_engine": "python_bm25_lite",
            "vector_ranking": True,
            "vector_engine": "python_cosine_exact_lexical",
            "vector_note": "Lexical TF-IDF vectors only; no neural embeddings.",
            "top_k": settings.RETRIEVAL_TOP_K,
            "pgvector": db_info.get("pgvector", False),
        },
        orchestration=orchestrator,
        optimiser=optimiser,
        counts=counts,
        warnings=warnings,
        providers={
            "gemini": gemini,
            "cerebras": cerebras,
            "deterministic": llm_status.get("deterministic"),
            "routing": llm_status.get("routing"),
            "requests_made": llm_status.get("requests_made"),
            "failed_requests": llm_status.get("failed_requests"),
            "input_tokens": llm_status.get("input_tokens"),
            "output_tokens": llm_status.get("output_tokens"),
            "total_tokens": llm_status.get("total_tokens"),
            "cerebras_ledger": ledger,
            "cache": llm_status.get("cache"),
            "fallback_events": llm_status.get("fallback_events"),
            "fallback_count": llm_status.get("fallback_count"),
        },
        execution_modes={
            "current": mode,
            "preferred_provider": llm_status.get("preferred_provider"),
            "fallback_provider": llm_status.get("fallback_provider"),
            "effective_deterministic": llm_status.get("effective_deterministic"),
        },
    )


@router.get("/system/providers", response_model=dict)
def provider_status() -> dict[str, Any]:
    """Focused hosted-AI observability for the dashboard (no secrets)."""
    from app.llm.router import get_router

    return get_router().provider_status_sync()


@router.get("/dashboard", response_model=DashboardOut)
def dashboard(
    db: Session = Depends(get_db), ctx: RequestContext = Depends(get_context)
) -> DashboardOut:
    from app.services.evidence import evidence_state_for_control
    from app.services.risk import risk_summary as _risk_summary

    from app.models.controls import Control, ObligationControlMapping
    from app.models.evidence import Evidence

    total_evidence = db.scalar(select(func.count()).select_from(Evidence)) or 0
    sufficient = db.scalar(
        select(func.count()).select_from(Evidence).where(Evidence.validity_status == "sufficient")
    ) or 0
    stale = db.scalar(
        select(func.count()).select_from(Evidence).where(Evidence.validity_status == "stale")
    ) or 0
    missing = db.scalar(
        select(func.count()).select_from(Evidence).where(Evidence.validity_status == "missing")
    ) or 0

    counts = {
        "regulations": db.scalar(select(func.count()).select_from(Document)) or 0,
        "obligations": db.scalar(select(func.count()).select_from(Obligation)) or 0,
        "obligations_applicable": db.scalar(
            select(func.count()).select_from(Obligation).where(Obligation.review_status == ReviewStatus.APPROVED)
        )
        or 0,
        "mapped_controls": db.scalar(
            select(func.count()).select_from(ObligationControlMapping).where(
                ObligationControlMapping.status == "approved"
            )
        )
        or 0,
        "open_gaps": db.scalar(
            select(func.count()).select_from(ComplianceGap).where(ComplianceGap.status == "open")
        )
        or 0,
        "high_priority_findings": db.scalar(
            select(func.count()).select_from(ComplianceGap).where(ComplianceGap.severity_hint == "high")
        )
        or 0,
        "overdue_remediation": db.scalar(
            select(func.count())
            .select_from(RemediationAction)
            .where(
                RemediationAction.deadline.is_not(None),
                RemediationAction.deadline < datetime.now(timezone.utc).date(),
                RemediationAction.status.in_(["approved", "in_progress"]),
            )
        )
        or 0,
        "changes_awaiting_assessment": db.scalar(
            select(func.count()).select_from(RegulatoryChange).where(
                RegulatoryChange.review_status == ReviewStatus.PENDING
            )
        )
        or 0,
    }

    gaps_by_type: dict[str, int] = {}
    for gap in db.scalars(select(ComplianceGap)):
        gaps_by_type[enum_value(gap.gap_type)] = gaps_by_type.get(enum_value(gap.gap_type), 0) + 1

    pending_reviews = {
        "obligations": db.scalar(
            select(func.count()).select_from(Obligation).where(Obligation.review_status == ReviewStatus.PENDING)
        )
        or 0,
        "mappings": db.scalar(
            select(func.count()).select_from(ObligationControlMapping).where(
                ObligationControlMapping.status == "candidate"
            )
        )
        or 0,
        "changes": db.scalar(
            select(func.count()).select_from(RegulatoryChange).where(
                RegulatoryChange.review_status == ReviewStatus.PENDING
            )
        )
        or 0,
        "remediation": db.scalar(
            select(func.count()).select_from(RemediationAction).where(
                RemediationAction.status == "proposed"
            )
        )
        or 0,
        "evidence": db.scalar(
            select(func.count()).select_from(Evidence).where(Evidence.review_status == ReviewStatus.PENDING)
        )
        or 0,
    }

    activity = [
        {
            "action": e.action,
            "actor": e.actor,
            "entity_type": e.entity_type,
            "entity_uid": e.entity_uid,
            "created_at": e.created_at.isoformat() if e.created_at else None,
            "detail": (e.detail or "")[:200],
        }
        for e in recent_activity(db, limit=15)
    ]

    return DashboardOut(
        counts=counts,
        risk=_risk_summary(db),
        evidence={
            "total": total_evidence,
            "sufficient": sufficient,
            "stale": stale,
            "missing": missing,
            "coverage": round(sufficient / total_evidence, 4) if total_evidence else 0.0,
        },
        recent_activity=activity,
        open_gaps_by_type=gaps_by_type,
        pending_reviews=pending_reviews,
        demo_ready=bool(counts["obligations"] and counts["open_gaps"]),
    )


# ---------------------------------------------------------------------------
# Impact
# ---------------------------------------------------------------------------
@router.post("/impact/analyze", response_model=ImpactOut)
def analyze_impact(
    payload: ImpactAnalyzeRequest,
    db: Session = Depends(get_db),
    ctx: RequestContext = Depends(get_context),
) -> ImpactOut:
    from app.services.impact import analyze_impact as _analyze

    change = db.get(RegulatoryChange, payload.change_id)
    if change is None:
        raise NotFoundError(f"Regulatory change {payload.change_id} not found")
    result = _analyze(db, payload.change_id, actor=payload.actor, recalculate_risk=payload.recalculate_risk)
    return ImpactOut.model_validate(result.impact)


@router.get("/impact/{impact_id}", response_model=ImpactOut)
def get_impact(
    impact_id: str, db: Session = Depends(get_db), ctx: RequestContext = Depends(get_context)
) -> ImpactOut:
    impact = db.get(ImpactAssessment, impact_id)
    if impact is None:
        raise NotFoundError(f"Impact assessment {impact_id} not found")
    return ImpactOut.model_validate(impact)


@router.get("/impact", response_model=dict)
def list_impacts(
    db: Session = Depends(get_db),
    ctx: RequestContext = Depends(get_context),
    change_id: str | None = None,
    page: int = 1,
    page_size: int = 25,
    sort: str = "created_at",
    order: str = "desc",
) -> dict[str, Any]:
    stmt = select(ImpactAssessment)
    if change_id:
        stmt = stmt.where(ImpactAssessment.change_id == change_id)
    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    column = getattr(ImpactAssessment, sort, ImpactAssessment.created_at)
    stmt = stmt.order_by(column.desc() if order == "desc" else column.asc())
    stmt = stmt.offset((page - 1) * page_size).limit(page_size)
    items = [ImpactOut.model_validate(i).model_dump(mode="json") for i in db.scalars(stmt)]
    return page_response(items, total, _params(page, page_size))


# ---------------------------------------------------------------------------
# Scenarios
# ---------------------------------------------------------------------------
@router.post("/scenarios", response_model=ScenarioOut, status_code=201)
def create_scenario(
    payload: ScenarioCreate, db: Session = Depends(get_db), ctx: RequestContext = Depends(get_context)
) -> ScenarioOut:
    import json
    import secrets

    scenario = RemediationScenario(
        scenario_uid=f"SCN-{secrets.token_hex(4).upper()}",
        name=payload.name,
        strategy=payload.strategy,
        description=payload.description,
        budget_inr=payload.budget_inr,
        capacity_days=payload.capacity_days,
        horizon_days=payload.horizon_days,
        created_by=payload.created_by,
        assumptions=json.dumps(
            {
                "team_capacity": payload.team_capacity,
                "action_ids": payload.action_ids,
                "gap_ids": payload.gap_ids,
            }
        ),
    )
    db.add(scenario)
    db.flush()
    record_audit(
        db,
        actor=payload.created_by,
        action="scenario.created",
        entity_type="remediation_scenario",
        entity_id=scenario.id,
        entity_uid=scenario.scenario_uid,
        correlation_id=ctx.correlation_id,
        after=payload.model_dump(mode="json"),
    )
    out = ScenarioOut.model_validate(scenario)
    # store the selection filters on the scenario for the simulate call
    scenario.assumptions = json.dumps(
        {
            "team_capacity": payload.team_capacity,
            "action_ids": payload.action_ids,
            "gap_ids": payload.gap_ids,
        }
    )
    return out


@router.post("/scenarios/{scenario_id}/simulate", response_model=ScenarioResultOut)
def simulate_scenario(
    scenario_id: str,
    use_cpsat: bool = Query(True),
    actor: str = Query("scenario_analysis_agent"),
    db: Session = Depends(get_db),
    ctx: RequestContext = Depends(get_context),
) -> ScenarioResultOut:
    import json

    from app.services.scenarios import CpsatOptimiser, GreedyOptimiser, run_scenario

    scenario = db.get(RemediationScenario, scenario_id)
    if scenario is None:
        raise NotFoundError(f"Scenario {scenario_id} not found")
    raw = scenario.assumptions or "{}"
    try:
        assumptions = json.loads(raw)
    except (TypeError, ValueError):
        assumptions = {}

    result = run_scenario(
        db,
        scenario,
        action_ids=assumptions.get("action_ids"),
        gap_ids=assumptions.get("gap_ids"),
        actor=actor,
    )
    if not use_cpsat or not CpsatOptimiser().available():
        # re-run with the deterministic fallback so the choice is explicit
        result = run_scenario(db, scenario, actor=actor)
        result.solver = GreedyOptimiser().name
    return ScenarioResultOut.model_validate(result)


@router.get("/scenarios/{scenario_id}/results", response_model=dict)
def scenario_results(
    scenario_id: str, db: Session = Depends(get_db), ctx: RequestContext = Depends(get_context)
) -> dict[str, Any]:
    scenario = db.get(RemediationScenario, scenario_id)
    if scenario is None:
        raise NotFoundError(f"Scenario {scenario_id} not found")
    results = list(
        db.scalars(select(ScenarioResult).where(ScenarioResult.scenario_id == scenario_id))
    )
    return {
        "scenario": ScenarioOut.model_validate(scenario).model_dump(mode="json"),
        "results": [ScenarioResultOut.model_validate(r).model_dump(mode="json") for r in results],
    }


@router.get("/scenarios", response_model=dict)
def list_scenarios(
    db: Session = Depends(get_db),
    ctx: RequestContext = Depends(get_context),
    page: int = 1,
    page_size: int = 25,
) -> dict[str, Any]:
    stmt = select(RemediationScenario).order_by(RemediationScenario.created_at.desc())
    total = db.scalar(select(func.count()).select_from(RemediationScenario)) or 0
    stmt = stmt.offset((page - 1) * page_size).limit(page_size)
    items = [ScenarioOut.model_validate(s).model_dump(mode="json") for s in db.scalars(stmt)]
    return page_response(items, total, _params(page, page_size))


# ---------------------------------------------------------------------------
# Analysis
# ---------------------------------------------------------------------------
@router.get("/analysis/contradictions", response_model=list[ContradictionOut])
def contradictions(
    db: Session = Depends(get_db), ctx: RequestContext = Depends(get_context)
) -> list[ContradictionOut]:
    from app.services.contradiction import detect_all

    report = detect_all(db)
    return [
        ContradictionOut(
            kind=f.kind,
            confidence=f.confidence,
            left_ref=f.left_ref,
            right_ref=f.right_ref,
            left_text=f.left_text,
            right_text=f.right_text,
            explanation=f.explanation,
            possible_explanation=f.possible_explanation,
            resolution_status=f.resolution_status,
        )
        for f in report.findings
    ]


@router.get("/analysis/cross-regulation", response_model=CrossRegulationOut)
def cross_regulation(
    floor: float = Query(0.62, ge=0.3, le=1.0),
    db: Session = Depends(get_db),
    ctx: RequestContext = Depends(get_context),
) -> CrossRegulationOut:
    from app.services.cross_regulation import build_report, report_to_json

    payload = report_to_json(build_report(db, floor=floor))
    return CrossRegulationOut(**payload)


# ---------------------------------------------------------------------------
# Audit & agents
# ---------------------------------------------------------------------------
@router.get("/audit/events", response_model=dict)
def list_audit_events(
    db: Session = Depends(get_db),
    ctx: RequestContext = Depends(get_context),
    action: str | None = None,
    entity_type: str | None = None,
    correlation_id: str | None = None,
    page: int = 1,
    page_size: int = 50,
    sort: str = "created_at",
    order: str = "desc",
) -> dict[str, Any]:
    stmt = select(AuditEvent)
    if action:
        stmt = stmt.where(AuditEvent.action == action)
    if entity_type:
        stmt = stmt.where(AuditEvent.entity_type == entity_type)
    if correlation_id:
        stmt = stmt.where(AuditEvent.correlation_id == correlation_id)
    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    column = getattr(AuditEvent, sort, AuditEvent.created_at)
    stmt = stmt.order_by(column.desc() if order == "desc" else column.asc())
    stmt = stmt.offset((page - 1) * page_size).limit(page_size)
    items = [AuditEventOut.model_validate(e).model_dump(mode="json") for e in db.scalars(stmt)]
    return page_response(items, total, _params(page, page_size))


@router.get("/agents/runs", response_model=dict)
def list_agent_runs(
    db: Session = Depends(get_db),
    ctx: RequestContext = Depends(get_context),
    status: str | None = None,
    workflow: str | None = None,
    page: int = 1,
    page_size: int = 20,
) -> dict[str, Any]:
    stmt = select(AgentRun)
    if status:
        stmt = stmt.where(AgentRun.status == status)
    if workflow:
        stmt = stmt.where(AgentRun.workflow == workflow)
    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    stmt = stmt.order_by(AgentRun.started_at.desc()).offset((page - 1) * page_size).limit(page_size)
    items = [AgentRunOut.model_validate(r).model_dump(mode="json") for r in db.scalars(stmt)]
    return page_response(items, total, _params(page, page_size))


@router.get("/agents/runs/{run_id}", response_model=AgentRunOut)
def get_agent_run(
    run_id: str, db: Session = Depends(get_db), ctx: RequestContext = Depends(get_context)
) -> AgentRunOut:
    run = db.get(AgentRun, run_id)
    if run is None:
        raise NotFoundError(f"Agent run {run_id} not found")
    return AgentRunOut.model_validate(run)


@router.get("/agents/catalog", response_model=list[dict])
def agent_catalog_endpoint() -> list[dict[str, Any]]:
    from app.agents import agent_catalog, list_workflows

    return [{"agents": agent_catalog(), "workflows": list_workflows()}]


@router.post("/agents/workflows/run", response_model=WorkflowRunResponse)
def run_workflow_endpoint(
    payload: WorkflowRunRequest,
    db: Session = Depends(get_db),
    ctx: RequestContext = Depends(get_context),
) -> WorkflowRunResponse:
    from app.agents import run_workflow as _run

    try:
        result = _run(db, payload.workflow, payload.params, actor=payload.actor)
    except KeyError as exc:
        raise ValidationError(str(exc)) from exc
    record_audit(
        db,
        actor=payload.actor,
        action="agent_workflow.triggered",
        entity_type="agent_run",
        entity_uid=result.get("run_uid"),
        correlation_id=ctx.correlation_id,
        after={"workflow": payload.workflow, "status": result.get("status")},
    )
    return WorkflowRunResponse(**result)


@router.get("/agents/workflows", response_model=list[dict])
def list_workflows_endpoint() -> list[dict[str, Any]]:
    from app.agents import list_workflows

    return list_workflows()


__all__ = ["router"]

