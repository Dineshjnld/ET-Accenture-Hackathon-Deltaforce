"""Shared model base classes and enumerations."""
from __future__ import annotations

import enum
import uuid
from datetime import datetime, timezone

from sqlalchemy import DateTime, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def enum_value(value: object) -> str:
    """Return the plain string value of a str-enum member or a plain string.

    Model columns are plain ``String`` (for SQLite/Postgres portability), so
    values loaded from the database are already ``str`` while newly assigned
    values may be enum members. Always normalise before ``.value`` access,
    persistence of display strings, or comparisons.
    """
    return getattr(value, "value", value)  # type: ignore[return-value]


def new_uuid() -> str:
    return str(uuid.uuid4())


class UUIDPrimaryKeyMixin:
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False
    )


class _EnumStr(str, enum.Enum):
    """String enumeration that serialises to its value."""

    def __str__(self) -> str:  # pragma: no cover - trivial
        return str(self.value)


class DocumentSourceKind(str, enum.Enum):
    OFFICIAL = "official"  # retrieved from an authoritative public source
    SYNTHETIC = "synthetic"  # clearly labelled sample/fictional content
    INTERNAL = "internal"  # the bank's own policy / evidence material


class ReviewStatus(str, enum.Enum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    NEEDS_REVISION = "needs_revision"


class Severity(str, enum.Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"
    UNKNOWN = "unknown"


class MappingStatus(str, enum.Enum):
    CANDIDATE = "candidate"
    APPROVED = "approved"
    REJECTED = "rejected"


class ControlType(str, enum.Enum):
    PREVENTIVE = "preventive"
    DETECTIVE = "detective"
    CORRECTIVE = "corrective"
    UNKNOWN = "unknown"


class AutomationLevel(str, enum.Enum):
    MANUAL = "manual"
    AUTOMATED = "automated"
    HYBRID = "hybrid"
    UNKNOWN = "unknown"


class DesignEffectiveness(str, enum.Enum):
    EFFECTIVE_BY_DESIGN = "effective_by_design"
    PARTIALLY_ADDRESSED = "partially_addressed"
    NOT_ADDRESSED = "not_addressed"
    UNABLE_TO_DETERMINE = "unable_to_determine"


class OperatingEffectiveness(str, enum.Enum):
    EFFECTIVE = "effective"
    PARTIALLY_EFFECTIVE = "partially_effective"
    INEFFECTIVE = "ineffective"
    NOT_TESTED = "not_tested"
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"


class EvidenceValidity(str, enum.Enum):
    SUFFICIENT = "sufficient"
    INSUFFICIENT = "insufficient"
    MISSING = "missing"
    STALE = "stale"
    INCONCLUSIVE = "inconclusive"
    NOT_REVIEWED = "not_reviewed"


class TestResult(str, enum.Enum):
    PASS = "pass"
    FAIL = "fail"
    INCONCLUSIVE = "inconclusive"
    NOT_PERFORMED = "not_performed"


class GapStatus(str, enum.Enum):
    OPEN = "open"
    IN_REMEDIATION = "in_remediation"
    CLOSED = "closed"
    ACCEPTED_RISK = "accepted_risk"


class GapType(str, enum.Enum):
    NO_CONTROL_MAPPED = "obligation_without_control"
    CONTROL_NOT_APPROVED = "control_mapping_not_approved"
    MISSING_EVIDENCE = "missing_evidence"
    STALE_EVIDENCE = "stale_evidence"
    FAILED_CONTROL_TEST = "failed_control_test"
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"
    INCOMPLETE_POLICY_COVERAGE = "incomplete_policy_coverage"
    CONFLICTING_POLICY = "conflicting_internal_policy"
    CHANGE_NOT_ASSESSED = "regulatory_change_not_assessed"
    REMEDIATION_OVERDUE = "remediation_overdue"
    DESIGN_GAP = "control_design_gap"


class RemediationStatus(str, enum.Enum):
    PROPOSED = "proposed"
    APPROVED = "approved"
    REJECTED = "rejected"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    CLOSED = "closed"
    OVERDUE = "overdue"


class ChangeType(str, enum.Enum):
    ADDED = "added"
    REMOVED = "removed"
    MODIFIED = "modified"
    DEADLINE_CHANGE = "deadline_change"
    APPLICABILITY_CHANGE = "applicability_change"
    THRESHOLD_CHANGE = "threshold_change"
    AMBIGUOUS = "requires_human_interpretation"


class AgentRunStatus(str, enum.Enum):
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    PARTIAL = "partial"
    TIMED_OUT = "timed_out"


class AgentEventStatus(str, enum.Enum):
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    SKIPPED = "skipped"
    RETRIED = "retried"


class ScenarioStrategy(str, enum.Enum):
    MAX_RISK_REDUCTION = "max_risk_reduction"
    DEADLINE_FIRST = "deadline_first"
    SHARED_CONTROL = "shared_control"
    CUSTOM = "custom"


__all__ = [
    "Base",
    "UUIDPrimaryKeyMixin",
    "TimestampMixin",
    "utcnow",
    "new_uuid",
    "enum_value",
    "DocumentSourceKind",
    "ReviewStatus",
    "Severity",
    "MappingStatus",
    "ControlType",
    "AutomationLevel",
    "DesignEffectiveness",
    "OperatingEffectiveness",
    "EvidenceValidity",
    "TestResult",
    "GapStatus",
    "GapType",
    "RemediationStatus",
    "ChangeType",
    "AgentRunStatus",
    "AgentEventStatus",
    "ScenarioStrategy",
]
