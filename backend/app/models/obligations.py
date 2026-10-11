"""Obligations, applicability, regulatory changes and impact assessments."""
from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import (
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
    ChangeType,
    ReviewStatus,
    Severity,
    TimestampMixin,
    UUIDPrimaryKeyMixin,
)


class Obligation(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "obligations"

    obligation_uid: Mapped[str] = mapped_column(String(40), unique=True, nullable=False)
    regulation_id: Mapped[str] = mapped_column(ForeignKey("regulations.id", ondelete="CASCADE"), index=True)
    regulation_version_id: Mapped[str] = mapped_column(
        ForeignKey("regulation_versions.id", ondelete="CASCADE"), index=True
    )
    source_document_id: Mapped[str | None] = mapped_column(
        ForeignKey("documents.id", ondelete="SET NULL"), nullable=True
    )
    source_document_version_id: Mapped[str | None] = mapped_column(
        ForeignKey("document_versions.id", ondelete="SET NULL"), nullable=True
    )
    source_page: Mapped[int | None] = mapped_column(Integer, nullable=True)
    source_section: Mapped[str | None] = mapped_column(String(255), nullable=True)
    quoted_text: Mapped[str] = mapped_column(Text, nullable=False)
    obligation_text: Mapped[str] = mapped_column(Text, nullable=False)
    responsible_party: Mapped[str | None] = mapped_column(String(255), nullable=True)
    action_required: Mapped[str | None] = mapped_column(String(255), nullable=True)
    applicability_conditions: Mapped[str | None] = mapped_column(Text, nullable=True)  # JSON list
    effective_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    deadline_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    deadline_text: Mapped[str | None] = mapped_column(String(255), nullable=True)
    jurisdiction: Mapped[str | None] = mapped_column(String(80), nullable=True)
    severity: Mapped[Severity] = mapped_column(String(20), default=Severity.UNKNOWN)
    severity_rationale: Mapped[str | None] = mapped_column(Text, nullable=True)
    extraction_confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    extraction_method: Mapped[str] = mapped_column(String(40), default="deterministic")
    extraction_model: Mapped[str | None] = mapped_column(String(160), nullable=True)
    review_status: Mapped[ReviewStatus] = mapped_column(String(30), default=ReviewStatus.PENDING)
    reviewed_by: Mapped[str | None] = mapped_column(String(160), nullable=True)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    review_notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_synthetic: Mapped[bool] = mapped_column(default=False)

    mappings: Mapped[list["ObligationControlMapping"]] = relationship(  # noqa: F821
        back_populates="obligation", cascade="all, delete-orphan"
    )
    applicability_assessments: Mapped[list["ApplicabilityAssessment"]] = relationship(
        back_populates="obligation", cascade="all, delete-orphan"
    )
    gaps: Mapped[list["ComplianceGap"]] = relationship(  # noqa: F821
        back_populates="obligation", cascade="all, delete-orphan"
    )

    __table_args__ = (
        Index("ix_obligations_review", "review_status"),
        Index("ix_obligations_severity", "severity"),
    )


class ApplicabilityRule(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "applicability_rules"

    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    rule_type: Mapped[str] = mapped_column(String(60), default="entity_scope")
    # JSON: [{"field": "entity_type", "operator": "in", "value": ["scheduled_commercial_bank"]}, ...]
    conditions: Mapped[str] = mapped_column(Text, nullable=False)
    outcome: Mapped[str] = mapped_column(String(30), default="applicable")  # applicable|not_applicable|unknown
    priority: Mapped[int] = mapped_column(Integer, default=100)
    is_active: Mapped[bool] = mapped_column(default=True)
    created_by: Mapped[str | None] = mapped_column(String(160), nullable=True)

    assessments: Mapped[list["ApplicabilityAssessment"]] = relationship(back_populates="rule")


class ApplicabilityAssessment(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "applicability_assessments"

    obligation_id: Mapped[str] = mapped_column(
        ForeignKey("obligations.id", ondelete="CASCADE"), index=True, nullable=True
    )
    rule_id: Mapped[str | None] = mapped_column(ForeignKey("applicability_rules.id", ondelete="SET NULL"), nullable=True)
    organization_id: Mapped[str | None] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), nullable=True, index=True
    )
    business_unit_id: Mapped[str | None] = mapped_column(
        ForeignKey("business_units.id", ondelete="SET NULL"), nullable=True
    )
    applicable: Mapped[str] = mapped_column(String(20), default="unknown")  # yes|no|unknown
    rationale: Mapped[str] = mapped_column(Text, nullable=False)
    evaluated_inputs: Mapped[str | None] = mapped_column(Text, nullable=True)  # JSON snapshot used for evaluation
    assessed_by: Mapped[str] = mapped_column(String(40), default="rule_engine")

    obligation: Mapped[Obligation | None] = relationship(back_populates="applicability_assessments")
    rule: Mapped[ApplicabilityRule | None] = relationship(back_populates="assessments")


class RegulatoryChange(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "regulatory_changes"

    change_uid: Mapped[str] = mapped_column(String(40), unique=True, nullable=False)
    regulation_id: Mapped[str] = mapped_column(ForeignKey("regulations.id", ondelete="CASCADE"), index=True)
    from_version_id: Mapped[str | None] = mapped_column(
        ForeignKey("regulation_versions.id", ondelete="SET NULL"), nullable=True
    )
    to_version_id: Mapped[str | None] = mapped_column(
        ForeignKey("regulation_versions.id", ondelete="SET NULL"), nullable=True
    )
    change_type: Mapped[ChangeType] = mapped_column(String(40), default=ChangeType.MODIFIED)
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    added_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    removed_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_reference: Mapped[str | None] = mapped_column(Text, nullable=True)  # JSON
    clause_ref: Mapped[str | None] = mapped_column(String(120), nullable=True)
    detected_by: Mapped[str] = mapped_column(String(40), default="diff_engine")
    confidence: Mapped[float] = mapped_column(Float, default=0.5)
    requires_human_review: Mapped[bool] = mapped_column(default=True)
    review_status: Mapped[ReviewStatus] = mapped_column(String(30), default=ReviewStatus.PENDING)
    reviewed_by: Mapped[str | None] = mapped_column(String(160), nullable=True)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    is_synthetic: Mapped[bool] = mapped_column(default=False)

    impact_assessments: Mapped[list["ImpactAssessment"]] = relationship(
        back_populates="change", cascade="all, delete-orphan"
    )


class ImpactAssessment(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "impact_assessments"

    impact_uid: Mapped[str] = mapped_column(String(40), unique=True, nullable=False)
    change_id: Mapped[str] = mapped_column(
        ForeignKey("regulatory_changes.id", ondelete="CASCADE"), index=True
    )
    affected_obligation_ids: Mapped[str | None] = mapped_column(Text, nullable=True)  # JSON list
    affected_control_ids: Mapped[str | None] = mapped_column(Text, nullable=True)  # JSON list
    affected_gap_ids: Mapped[str | None] = mapped_column(Text, nullable=True)  # JSON list
    affected_action_ids: Mapped[str | None] = mapped_column(Text, nullable=True)  # JSON list
    affected_mapping_ids: Mapped[str | None] = mapped_column(Text, nullable=True)  # JSON list
    impact_score: Mapped[float] = mapped_column(Float, default=0.0)
    priority_delta: Mapped[float] = mapped_column(Float, default=0.0)
    narrative: Mapped[str] = mapped_column(Text, nullable=False)
    uncertainty_flags: Mapped[str | None] = mapped_column(Text, nullable=True)  # JSON list
    propagation_path: Mapped[str | None] = mapped_column(Text, nullable=True)  # JSON audit of traversal
    status: Mapped[str] = mapped_column(String(30), default="draft")  # draft|reviewed|approved
    created_by: Mapped[str] = mapped_column(String(40), default="impact_agent")
    agent_run_id: Mapped[str | None] = mapped_column(String(36), nullable=True)

    change: Mapped[RegulatoryChange] = relationship(back_populates="impact_assessments")

    __table_args__ = (Index("ix_impact_status", "status"),)


__all__ = [
    "Obligation",
    "ApplicabilityRule",
    "ApplicabilityAssessment",
    "RegulatoryChange",
    "ImpactAssessment",
]
