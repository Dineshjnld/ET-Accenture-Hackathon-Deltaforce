"""Internal policies, controls and obligation-to-control mappings."""
from __future__ import annotations

from datetime import date, datetime

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
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base
from app.models.core import (
    AutomationLevel,
    ControlType,
    DesignEffectiveness,
    MappingStatus,
    OperatingEffectiveness,
    ReviewStatus,
    TimestampMixin,
    UUIDPrimaryKeyMixin,
)


class Policy(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "policies"

    code: Mapped[str] = mapped_column(String(60), unique=True, nullable=False)
    title: Mapped[str] = mapped_column(String(400), nullable=False)
    owner_role: Mapped[str | None] = mapped_column(String(160), nullable=True)
    category: Mapped[str | None] = mapped_column(String(120), nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_document_id: Mapped[str | None] = mapped_column(
        ForeignKey("documents.id", ondelete="SET NULL"), nullable=True
    )

    versions: Mapped[list["PolicyVersion"]] = relationship(
        back_populates="policy", cascade="all, delete-orphan", order_by="PolicyVersion.effective_from"
    )


class PolicyVersion(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "policy_versions"

    policy_id: Mapped[str] = mapped_column(ForeignKey("policies.id", ondelete="CASCADE"), index=True)
    version_label: Mapped[str] = mapped_column(String(80), nullable=False)
    effective_from: Mapped[date | None] = mapped_column(Date, nullable=True)
    effective_to: Mapped[date | None] = mapped_column(Date, nullable=True)
    content_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    approved_by: Mapped[str | None] = mapped_column(String(160), nullable=True)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    status: Mapped[str] = mapped_column(String(30), default="draft")  # draft|approved|superseded|withdrawn

    policy: Mapped[Policy] = relationship(back_populates="versions")
    controls: Mapped[list["Control"]] = relationship(back_populates="policy_version")


class Control(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "controls"

    control_uid: Mapped[str] = mapped_column(String(40), unique=True, nullable=False)
    title: Mapped[str] = mapped_column(String(400), nullable=False)
    policy_version_id: Mapped[str | None] = mapped_column(
        ForeignKey("policy_versions.id", ondelete="SET NULL"), nullable=True
    )
    business_unit_id: Mapped[str | None] = mapped_column(
        ForeignKey("business_units.id", ondelete="SET NULL"), nullable=True
    )
    control_objective: Mapped[str | None] = mapped_column(Text, nullable=True)
    control_activity: Mapped[str | None] = mapped_column(Text, nullable=True)
    owner_role: Mapped[str | None] = mapped_column(String(160), nullable=True)
    frequency: Mapped[str | None] = mapped_column(String(80), nullable=True)
    control_type: Mapped[ControlType] = mapped_column(String(20), default=ControlType.UNKNOWN)
    automation: Mapped[AutomationLevel] = mapped_column(String(20), default=AutomationLevel.UNKNOWN)
    expected_evidence: Mapped[str | None] = mapped_column(Text, nullable=True)
    testing_procedure: Mapped[str | None] = mapped_column(Text, nullable=True)
    design_effectiveness: Mapped[DesignEffectiveness] = mapped_column(
        String(40), default=DesignEffectiveness.UNABLE_TO_DETERMINE
    )
    operating_effectiveness: Mapped[OperatingEffectiveness] = mapped_column(
        String(40), default=OperatingEffectiveness.NOT_TESTED
    )
    design_rationale: Mapped[str | None] = mapped_column(Text, nullable=True)
    operating_rationale: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_tested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    status: Mapped[ReviewStatus] = mapped_column(String(30), default=ReviewStatus.PENDING)
    source_document_id: Mapped[str | None] = mapped_column(
        ForeignKey("documents.id", ondelete="SET NULL"), nullable=True
    )
    extraction_method: Mapped[str] = mapped_column(String(40), default="manual")
    is_synthetic: Mapped[bool] = mapped_column(default=False)

    policy_version: Mapped[PolicyVersion | None] = relationship(back_populates="controls")
    mappings: Mapped[list["ObligationControlMapping"]] = relationship(
        back_populates="control", cascade="all, delete-orphan"
    )
    evidence_links: Mapped[list["EvidenceControlLink"]] = relationship(  # noqa: F821
        back_populates="control", cascade="all, delete-orphan"
    )
    tests: Mapped[list["ControlTest"]] = relationship(back_populates="control", cascade="all, delete-orphan")  # noqa: F821

    __table_args__ = (Index("ix_controls_status", "status"), Index("ix_controls_type", "control_type"))


class ControlVersion(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "control_versions"

    control_id: Mapped[str] = mapped_column(ForeignKey("controls.id", ondelete="CASCADE"), index=True)
    version_label: Mapped[str] = mapped_column(String(80), nullable=False)
    snapshot: Mapped[str] = mapped_column(Text, nullable=False)  # JSON snapshot
    effective_from: Mapped[date | None] = mapped_column(Date, nullable=True)
    created_by: Mapped[str | None] = mapped_column(String(160), nullable=True)


class ObligationControlMapping(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "obligation_control_mappings"

    obligation_id: Mapped[str] = mapped_column(
        ForeignKey("obligations.id", ondelete="CASCADE"), index=True
    )
    control_id: Mapped[str] = mapped_column(ForeignKey("controls.id", ondelete="CASCADE"), index=True)
    mapping_rationale: Mapped[str] = mapped_column(Text, nullable=False)
    source_references: Mapped[str | None] = mapped_column(Text, nullable=True)  # JSON
    match_signals: Mapped[str | None] = mapped_column(Text, nullable=True)  # JSON: lexical/semantic scores
    confidence: Mapped[float] = mapped_column(Float, default=0.0)
    status: Mapped[MappingStatus] = mapped_column(String(30), default=MappingStatus.CANDIDATE)
    proposed_by: Mapped[str] = mapped_column(String(40), default="rule")  # rule|llm|user|hybrid
    proposed_model: Mapped[str | None] = mapped_column(String(160), nullable=True)
    reviewer_decision: Mapped[str | None] = mapped_column(String(30), nullable=True)
    reviewed_by: Mapped[str | None] = mapped_column(String(160), nullable=True)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    reject_reason: Mapped[str | None] = mapped_column(Text, nullable=True)

    obligation: Mapped[Obligation] = relationship(back_populates="mappings")
    control: Mapped[Control] = relationship(back_populates="mappings")

    __table_args__ = (
        Index("ix_mappings_status", "status"),
        Index("ix_mappings_pair", "obligation_id", "control_id"),
    )


__all__ = [
    "Policy",
    "PolicyVersion",
    "Control",
    "ControlVersion",
    "ObligationControlMapping",
]
