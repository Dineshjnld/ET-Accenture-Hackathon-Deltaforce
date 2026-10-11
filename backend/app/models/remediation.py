"""Compliance gaps, risk assessments, remediation and scenarios."""
from __future__ import annotations

from datetime import date, datetime

from app.models.core import utcnow as utc_now
from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base
from app.models.core import (
    GapStatus,
    GapType,
    RemediationStatus,
    ReviewStatus,
    ScenarioStrategy,
    Severity,
    TimestampMixin,
    UUIDPrimaryKeyMixin,
)


class ComplianceGap(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "compliance_gaps"

    gap_uid: Mapped[str] = mapped_column(String(40), unique=True, nullable=False)
    obligation_id: Mapped[str | None] = mapped_column(
        ForeignKey("obligations.id", ondelete="CASCADE"), nullable=True, index=True
    )
    control_id: Mapped[str | None] = mapped_column(
        ForeignKey("controls.id", ondelete="SET NULL"), nullable=True, index=True
    )
    business_unit_id: Mapped[str | None] = mapped_column(
        ForeignKey("business_units.id", ondelete="SET NULL"), nullable=True
    )
    gap_type: Mapped[GapType] = mapped_column(String(50), nullable=False)
    title: Mapped[str] = mapped_column(String(400), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    detection_basis: Mapped[str] = mapped_column(Text, nullable=False)  # human-readable explanation
    detection_context: Mapped[str | None] = mapped_column(Text, nullable=True)  # JSON: ids of ev/tests/changes
    detected_by: Mapped[str] = mapped_column(String(40), default="gap_agent")
    status: Mapped[GapStatus] = mapped_column(String(30), default=GapStatus.OPEN)
    severity_hint: Mapped[Severity] = mapped_column(String(20), default=Severity.UNKNOWN)
    truth_status: Mapped[str] = mapped_column(
        String(30), default="determinate"
    )  # determinate|indeterminate (cannot determine vs known failure)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    closure_evidence: Mapped[str | None] = mapped_column(Text, nullable=True)
    accepted_risk_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    approved_by: Mapped[str | None] = mapped_column(String(160), nullable=True)

    obligation: Mapped["Obligation | None"] = relationship(back_populates="gaps")
    control: Mapped["Control | None"] = relationship(foreign_keys="ComplianceGap.control_id")
    risk_assessments: Mapped[list["RiskAssessment"]] = relationship(
        back_populates="gap", cascade="all, delete-orphan"
    )
    remediation_actions: Mapped[list["RemediationAction"]] = relationship(
        back_populates="gap", cascade="all, delete-orphan"
    )

    __table_args__ = (Index("ix_gaps_status", "status"), Index("ix_gaps_type", "gap_type"))


class RiskAssessment(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "risk_assessments"

    gap_id: Mapped[str] = mapped_column(ForeignKey("compliance_gaps.id", ondelete="CASCADE"), index=True)
    regulatory_severity: Mapped[float] = mapped_column(Float, default=0.0)
    failure_likelihood: Mapped[float] = mapped_column(Float, default=0.0)
    exposure: Mapped[float] = mapped_column(Float, default=0.0)
    urgency: Mapped[float] = mapped_column(Float, default=0.0)
    evidence_confidence: Mapped[float] = mapped_column(Float, default=0.0)
    existing_mitigation: Mapped[float] = mapped_column(Float, default=0.0)
    weights: Mapped[str] = mapped_column(Text, nullable=False)  # JSON
    score: Mapped[float] = mapped_column(Float, nullable=False)
    score_band: Mapped[str] = mapped_column(String(20), nullable=False)
    hard_deadline: Mapped[date | None] = mapped_column(Date, nullable=True)
    days_to_deadline: Mapped[int | None] = mapped_column(Integer, nullable=True)
    deadline_breached: Mapped[bool] = mapped_column(default=False)
    hard_constraint_flag: Mapped[str | None] = mapped_column(String(120), nullable=True)
    explanation: Mapped[str] = mapped_column(Text, nullable=False)  # JSON with per-factor contributions
    calibrated: Mapped[bool] = mapped_column(default=False)
    calculated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
    calculated_by: Mapped[str] = mapped_column(String(40), default="risk_agent")

    gap: Mapped[ComplianceGap] = relationship(back_populates="risk_assessments")

    __table_args__ = (Index("ix_risk_score", "score"),)


class RemediationAction(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "remediation_actions"

    action_uid: Mapped[str] = mapped_column(String(40), unique=True, nullable=False)
    gap_id: Mapped[str] = mapped_column(ForeignKey("compliance_gaps.id", ondelete="CASCADE"), index=True)
    proposed_action: Mapped[str] = mapped_column(Text, nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    expected_risk_reduction: Mapped[float] = mapped_column(Float, default=0.0)
    estimated_cost_inr: Mapped[float | None] = mapped_column(Float, nullable=True)
    cost_is_estimate: Mapped[bool] = mapped_column(default=True)
    estimated_duration_days: Mapped[int | None] = mapped_column(Integer, nullable=True)
    duration_is_estimate: Mapped[bool] = mapped_column(default=True)
    suggested_owner_role: Mapped[str | None] = mapped_column(String(160), nullable=True)
    dependencies: Mapped[str | None] = mapped_column(Text, nullable=True)  # JSON list of action ids
    deadline: Mapped[date | None] = mapped_column(Date, nullable=True)
    verification_procedure: Mapped[str | None] = mapped_column(Text, nullable=True)
    required_evidence_for_closure: Mapped[str | None] = mapped_column(Text, nullable=True)
    approval_required: Mapped[bool] = mapped_column(default=True)
    status: Mapped[RemediationStatus] = mapped_column(String(30), default=RemediationStatus.PROPOSED)
    approved_by: Mapped[str | None] = mapped_column(String(160), nullable=True)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    rejected_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    closure_evidence: Mapped[str | None] = mapped_column(Text, nullable=True)
    uncertainty_notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    generated_by: Mapped[str] = mapped_column(String(40), default="remediation_agent")
    required_capability: Mapped[str | None] = mapped_column(String(120), nullable=True)

    gap: Mapped[ComplianceGap] = relationship(back_populates="remediation_actions")
    dependencies_out: Mapped[list["RemediationDependency"]] = relationship(
        back_populates="action",
        foreign_keys="RemediationDependency.action_id",
        cascade="all, delete-orphan",
    )

    __table_args__ = (
        Index("ix_remediation_status", "status"),
        Index("ix_remediation_deadline", "deadline"),
    )


class RemediationDependency(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "remediation_dependencies"

    action_id: Mapped[str] = mapped_column(
        ForeignKey("remediation_actions.id", ondelete="CASCADE"), index=True
    )
    depends_on_action_id: Mapped[str] = mapped_column(
        ForeignKey("remediation_actions.id", ondelete="CASCADE"), index=True
    )
    dependency_type: Mapped[str] = mapped_column(String(40), default="finish_to_start")

    action: Mapped[RemediationAction] = relationship(back_populates="dependencies_out", foreign_keys=[action_id])


class RemediationScenario(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "remediation_scenarios"

    scenario_uid: Mapped[str] = mapped_column(String(40), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    strategy: Mapped[ScenarioStrategy] = mapped_column(String(40), default=ScenarioStrategy.MAX_RISK_REDUCTION)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    budget_inr: Mapped[float | None] = mapped_column(Float, nullable=True)
    capacity_days: Mapped[float | None] = mapped_column(Integer, nullable=True)
    horizon_days: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_by: Mapped[str | None] = mapped_column(String(160), nullable=True)
    assumptions: Mapped[str | None] = mapped_column(Text, nullable=True)  # JSON

    results: Mapped[list["ScenarioResult"]] = relationship(
        back_populates="scenario", cascade="all, delete-orphan", order_by="ScenarioResult.created_at"
    )


class ScenarioResult(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "scenario_results"

    scenario_id: Mapped[str] = mapped_column(
        ForeignKey("remediation_scenarios.id", ondelete="CASCADE"), index=True
    )
    selected_actions: Mapped[str] = mapped_column(Text, nullable=False)  # JSON
    rejected_actions: Mapped[str] = mapped_column(Text, nullable=False)  # JSON with reasons
    constraint_checks: Mapped[str] = mapped_column(Text, nullable=False)  # JSON
    objective_value: Mapped[float] = mapped_column(Float, nullable=False)
    projected_risk_reduction: Mapped[float] = mapped_column(Float, default=0.0)
    projected_cost_inr: Mapped[float] = mapped_column(Float, default=0.0)
    projected_duration_days: Mapped[float] = mapped_column(Float, default=0.0)
    unresolved_gap_ids: Mapped[str] = mapped_column(Text, nullable=False)  # JSON
    solver: Mapped[str] = mapped_column(String(60), nullable=False)
    optimality_claim: Mapped[str] = mapped_column(Text, nullable=False)
    explanation: Mapped[str] = mapped_column(Text, nullable=False)  # JSON
    assumptions: Mapped[str | None] = mapped_column(Text, nullable=True)  # JSON
    is_prediction: Mapped[bool] = mapped_column(default=True)

    scenario: Mapped[RemediationScenario] = relationship(back_populates="results")


__all__ = [
    "ComplianceGap",
    "RiskAssessment",
    "RemediationAction",
    "RemediationDependency",
    "RemediationScenario",
    "ScenarioResult",
]
