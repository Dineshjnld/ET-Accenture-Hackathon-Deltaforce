"""Evidence, control tests and test results."""
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
    EvidenceValidity,
    ReviewStatus,
    TestResult,
    TimestampMixin,
    UUIDPrimaryKeyMixin,
)


class Evidence(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "evidence"

    evidence_uid: Mapped[str] = mapped_column(String(40), unique=True, nullable=False)
    file_id: Mapped[str | None] = mapped_column(ForeignKey("documents.id", ondelete="SET NULL"), nullable=True)
    title: Mapped[str] = mapped_column(String(400), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    evidence_period_start: Mapped[date | None] = mapped_column(Date, nullable=True)
    evidence_period_end: Mapped[date | None] = mapped_column(Date, nullable=True)
    source: Mapped[str | None] = mapped_column(String(255), nullable=True)
    collection_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    validity_status: Mapped[EvidenceValidity] = mapped_column(
        String(30), default=EvidenceValidity.NOT_REVIEWED
    )
    validity_rationale: Mapped[str | None] = mapped_column(Text, nullable=True)
    freshness_status: Mapped[str] = mapped_column(String(20), default="unknown")  # fresh|stale|unknown
    review_status: Mapped[ReviewStatus] = mapped_column(String(30), default=ReviewStatus.PENDING)
    test_result: Mapped[TestResult | None] = mapped_column(String(20), nullable=True)
    test_result_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_synthetic: Mapped[bool] = mapped_column(default=False)
    uploaded_by: Mapped[str | None] = mapped_column(String(160), nullable=True)

    control_links: Mapped[list["EvidenceControlLink"]] = relationship(
        back_populates="evidence", cascade="all, delete-orphan"
    )

    __table_args__ = (
        Index("ix_evidence_validity", "validity_status"),
        Index("ix_evidence_period", "evidence_period_end"),
    )


class EvidenceControlLink(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "evidence_control_links"

    evidence_id: Mapped[str] = mapped_column(ForeignKey("evidence.id", ondelete="CASCADE"), index=True)
    control_id: Mapped[str] = mapped_column(ForeignKey("controls.id", ondelete="CASCADE"), index=True)
    link_type: Mapped[str] = mapped_column(String(40), default="supports")  # supports|contradicts|partial
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_by: Mapped[str | None] = mapped_column(String(160), nullable=True)

    evidence: Mapped[Evidence] = relationship(back_populates="control_links")
    control: Mapped[Control] = relationship(back_populates="evidence_links")


class ControlTest(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "control_tests"

    test_uid: Mapped[str] = mapped_column(String(40), unique=True, nullable=False)
    control_id: Mapped[str] = mapped_column(ForeignKey("controls.id", ondelete="CASCADE"), index=True)
    test_type: Mapped[str] = mapped_column(String(20), default="operating")  # design|operating
    test_procedure: Mapped[str] = mapped_column(Text, nullable=False)
    planned_period_start: Mapped[date | None] = mapped_column(Date, nullable=True)
    planned_period_end: Mapped[date | None] = mapped_column(Date, nullable=True)
    sample_size: Mapped[int | None] = mapped_column(Integer, nullable=True)
    performed_by: Mapped[str | None] = mapped_column(String(160), nullable=True)
    performed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    result: Mapped[TestResult] = mapped_column(String(20), default=TestResult.NOT_PERFORMED)
    result_rationale: Mapped[str | None] = mapped_column(Text, nullable=True)
    exceptions_found: Mapped[int] = mapped_column(Integer, default=0)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_synthetic: Mapped[bool] = mapped_column(default=False)

    control: Mapped[Control] = relationship(back_populates="tests")
    results: Mapped[list["TestResult"]] = relationship(  # noqa: F821
        back_populates="control_test", cascade="all, delete-orphan"
    )

    __table_args__ = (Index("ix_control_tests_result", "result"),)


class TestResult(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "test_results"

    control_test_id: Mapped[str] = mapped_column(
        ForeignKey("control_tests.id", ondelete="CASCADE"), index=True
    )
    evidence_id: Mapped[str | None] = mapped_column(
        ForeignKey("evidence.id", ondelete="SET NULL"), nullable=True
    )
    outcome: Mapped[TestResult] = mapped_column(String(20), nullable=False)
    observations: Mapped[str] = mapped_column(Text, nullable=False)
    sample_reference: Mapped[str | None] = mapped_column(String(255), nullable=True)
    recorded_by: Mapped[str | None] = mapped_column(String(160), nullable=True)

    control_test: Mapped[ControlTest] = relationship(back_populates="results")


__all__ = ["Evidence", "EvidenceControlLink", "ControlTest", "TestResult"]
