"""Agent runs/events, audit trail and approvals."""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    Boolean,
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
    AgentEventStatus,
    AgentRunStatus,
    ReviewStatus,
    TimestampMixin,
    UUIDPrimaryKeyMixin,
    utcnow,
)


class AgentRun(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "agent_runs"

    run_uid: Mapped[str] = mapped_column(String(40), unique=True, nullable=False)
    workflow: Mapped[str] = mapped_column(String(80), nullable=False, index=True)
    correlation_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    orchestration_backend: Mapped[str] = mapped_column(String(40), default="native")
    deterministic_mode: Mapped[bool] = mapped_column(default=True)
    status: Mapped[AgentRunStatus] = mapped_column(String(20), default=AgentRunStatus.RUNNING, index=True)
    triggered_by: Mapped[str | None] = mapped_column(String(160), nullable=True)
    trigger_source: Mapped[str] = mapped_column(String(40), default="api")
    config: Mapped[str | None] = mapped_column(Text, nullable=True)  # JSON
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)  # JSON
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)

    events: Mapped[list["AgentEvent"]] = relationship(
        back_populates="run", cascade="all, delete-orphan", order_by="AgentEvent.sequence"
    )


class AgentEvent(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "agent_events"

    run_id: Mapped[str] = mapped_column(ForeignKey("agent_runs.id", ondelete="CASCADE"), index=True)
    agent_name: Mapped[str] = mapped_column(String(80), nullable=False, index=True)
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[AgentEventStatus] = mapped_column(String(20), nullable=False)
    attempt: Mapped[int] = mapped_column(Integer, default=1)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    input_digest: Mapped[str | None] = mapped_column(String(64), nullable=True)
    output_digest: Mapped[str | None] = mapped_column(String(64), nullable=True)
    message: Mapped[str | None] = mapped_column(Text, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    model_used: Mapped[str | None] = mapped_column(String(160), nullable=True)
    rule_used: Mapped[str | None] = mapped_column(String(160), nullable=True)
    warnings: Mapped[str | None] = mapped_column(Text, nullable=True)  # JSON list
    citations: Mapped[str | None] = mapped_column(Text, nullable=True)  # JSON list
    output: Mapped[str | None] = mapped_column(Text, nullable=True)  # JSON, size-capped
    retries: Mapped[int] = mapped_column(Integer, default=0)

    run: Mapped[AgentRun] = relationship(back_populates="events")

    __table_args__ = (Index("ix_agent_events_agent", "agent_name", "status"),)


class AuditEvent(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """Append-oriented audit trail. Never updated, never deleted."""

    __tablename__ = "audit_events"

    event_uid: Mapped[str] = mapped_column(String(40), unique=True, nullable=False)
    actor: Mapped[str] = mapped_column(String(160), nullable=False)
    actor_role: Mapped[str | None] = mapped_column(String(80), nullable=True)
    action: Mapped[str] = mapped_column(String(120), nullable=False, index=True)
    entity_type: Mapped[str] = mapped_column(String(80), nullable=False, index=True)
    entity_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    entity_uid: Mapped[str | None] = mapped_column(String(40), nullable=True)
    correlation_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    run_uid: Mapped[str | None] = mapped_column(String(40), nullable=True)
    before: Mapped[str | None] = mapped_column(Text, nullable=True)  # JSON
    after: Mapped[str | None] = mapped_column(Text, nullable=True)  # JSON
    request_path: Mapped[str | None] = mapped_column(String(400), nullable=True)
    request_method: Mapped[str | None] = mapped_column(String(10), nullable=True)
    ip_address: Mapped[str | None] = mapped_column(String(64), nullable=True)
    detail: Mapped[str | None] = mapped_column(Text, nullable=True)

    __table_args__ = (Index("ix_audit_created", "created_at"),)


class Approval(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "approvals"

    entity_type: Mapped[str] = mapped_column(String(80), nullable=False)
    entity_id: Mapped[str] = mapped_column(String(36), nullable=False)
    decision: Mapped[str] = mapped_column(String(30), nullable=False)  # approved|rejected|revoked
    decided_by: Mapped[str] = mapped_column(String(160), nullable=False)
    decided_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    review_status_before: Mapped[str | None] = mapped_column(String(30), nullable=True)
    review_status_after: Mapped[str | None] = mapped_column(String(30), nullable=True)


__all__ = ["AgentRun", "AgentEvent", "AuditEvent", "Approval"]
