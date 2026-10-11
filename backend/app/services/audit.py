"""Append-only audit trail and stable business identifier generation."""
from __future__ import annotations

import secrets
from datetime import datetime
from typing import Any, Iterable

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.logging_config import get_logger
from app.models.audit import Approval, AuditEvent

log = get_logger("regulens.audit")

_PREFIXES = {
    "document": "DOC",
    "document_version": "DOCV",
    "regulation": "REG",
    "regulation_version": "REGV",
    "obligation": "OBL",
    "control": "CTL",
    "policy": "POL",
    "evidence": "EVD",
    "control_test": "TST",
    "gap": "GAP",
    "remediation": "REM",
    "scenario": "SCN",
    "change": "CHG",
    "impact": "IMP",
    "run": "RUN",
}


def generate_uid(kind: str, session: Session | None = None) -> str:
    prefix = _PREFIXES.get(kind, "OBJ")
    while True:
        candidate = f"{prefix}-{datetime.utcnow().strftime('%y%m%d')}-{secrets.token_hex(3).upper()}"
        yield_once = candidate
        if session is None:
            return yield_once
        if not _uid_exists(session, kind, yield_once):
            return yield_once


def _uid_exists(session: Session, kind: str, uid: str) -> bool:
    from app.models.audit import AgentRun
    from app.models.controls import Control, ObligationControlMapping, Policy
    from app.models.evidence import ControlTest, Evidence
    from app.models.obligations import ImpactAssessment, Obligation, RegulatoryChange
    from app.models.regulatory import Document, DocumentVersion, Regulation, RegulationVersion
    from app.models.remediation import ComplianceGap, RemediationAction, RemediationScenario

    table = {
        "document": (Document, "stored_filename"),
        "regulation": (Regulation, "code"),
        "regulation_version": (RegulationVersion, "version_label"),
        "obligation": (Obligation, "obligation_uid"),
        "control": (Control, "control_uid"),
        "policy": (Policy, "code"),
        "evidence": (Evidence, "evidence_uid"),
        "control_test": (ControlTest, "test_uid"),
        "gap": (ComplianceGap, "gap_uid"),
        "remediation": (RemediationAction, "action_uid"),
        "scenario": (RemediationScenario, "scenario_uid"),
        "change": (RegulatoryChange, "change_uid"),
        "impact": (ImpactAssessment, "impact_uid"),
        "run": (AgentRun, "run_uid"),
    }.get(kind)
    if table is None:
        return False
    model, column = table
    exists = session.scalar(select(getattr(model, column)).limit(1)) is not None
    # Uniqueness is enforced by DB constraints; this light check only guards the
    # random-token collision case for the primary uid columns.
    if not exists:
        return False
    col = getattr(model, column)
    return session.scalar(select(col).where(col == uid).limit(1)) is not None


def record_audit(
    session: Session,
    *,
    actor: str,
    action: str,
    entity_type: str,
    entity_id: str | None = None,
    entity_uid: str | None = None,
    correlation_id: str | None = None,
    run_uid: str | None = None,
    before: dict[str, Any] | None = None,
    after: dict[str, Any] | None = None,
    actor_role: str | None = None,
    request_path: str | None = None,
    request_method: str | None = None,
    ip_address: str | None = None,
    detail: str | None = None,
) -> AuditEvent:
    """Append an audit event. This is the only write path to ``audit_events``."""
    event = AuditEvent(
        event_uid=f"AUD-{secrets.token_hex(6).upper()}",
        actor=actor,
        actor_role=actor_role,
        action=action,
        entity_type=entity_type,
        entity_id=entity_id,
        entity_uid=entity_uid,
        correlation_id=correlation_id,
        run_uid=run_uid,
        before=_dump(before),
        after=_dump(after),
        request_path=request_path,
        request_method=request_method,
        ip_address=ip_address,
        detail=detail,
    )
    session.add(event)
    session.flush()
    return event


def record_approval(
    session: Session,
    *,
    entity_type: str,
    entity_id: str,
    decision: str,
    decided_by: str,
    reason: str | None = None,
    before_status: str | None = None,
    after_status: str | None = None,
) -> Approval:
    approval = Approval(
        entity_type=entity_type,
        entity_id=entity_id,
        decision=decision,
        decided_by=decided_by,
        reason=reason,
        review_status_before=before_status,
        review_status_after=after_status,
    )
    session.add(approval)
    session.flush()
    return approval


def _dump(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        return value
    import json

    try:
        return json.dumps(value, default=str)[:20000]
    except (TypeError, ValueError):  # pragma: no cover
        return str(value)[:20000]


def recent_activity(session: Session, limit: int = 25) -> Iterable[AuditEvent]:
    stmt = select(AuditEvent).order_by(AuditEvent.created_at.desc()).limit(limit)
    return list(session.scalars(stmt))


__all__ = ["record_audit", "record_approval", "generate_uid", "recent_activity"]
