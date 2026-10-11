"""Model registry — import once to populate ``Base.metadata``."""
from __future__ import annotations

from app.models.core import (  # noqa: F401
    AgentEventStatus,
    AgentRunStatus,
    AutomationLevel,
    Base,
    ChangeType,
    ControlType,
    DesignEffectiveness,
    DocumentSourceKind,
    EvidenceValidity,
    GapStatus,
    GapType,
    MappingStatus,
    OperatingEffectiveness,
    RemediationStatus,
    ReviewStatus,
    ScenarioStrategy,
    Severity,
    TestResult,
    TimestampMixin,
    UUIDPrimaryKeyMixin,
)
from app.models.identity import BusinessUnit, Organization, Role, User  # noqa: F401
from app.models.regulatory import (  # noqa: F401
    Document,
    DocumentChunk,
    DocumentVersion,
    Regulation,
    RegulationVersion,
    RegulatorySource,
)
from app.models.obligations import (  # noqa: F401
    ApplicabilityAssessment,
    ApplicabilityRule,
    ImpactAssessment,
    Obligation,
    RegulatoryChange,
)
from app.models.controls import (  # noqa: F401
    Control,
    ControlVersion,
    ObligationControlMapping,
    Policy,
    PolicyVersion,
)
from app.models.evidence import ControlTest, Evidence, EvidenceControlLink, TestResult  # noqa: F401
from app.models.remediation import (  # noqa: F401
    ComplianceGap,
    RemediationAction,
    RemediationDependency,
    RemediationScenario,
    RiskAssessment,
    ScenarioResult,
)
from app.models.audit import AgentEvent, AgentRun, Approval, AuditEvent  # noqa: F401

__all__ = [
    "Base",
    "Organization",
    "BusinessUnit",
    "Role",
    "User",
    "RegulatorySource",
    "Regulation",
    "RegulationVersion",
    "Document",
    "DocumentVersion",
    "DocumentChunk",
    "Obligation",
    "ApplicabilityRule",
    "ApplicabilityAssessment",
    "RegulatoryChange",
    "ImpactAssessment",
    "Policy",
    "PolicyVersion",
    "Control",
    "ControlVersion",
    "ObligationControlMapping",
    "Evidence",
    "EvidenceControlLink",
    "ControlTest",
    "TestResult",
    "ComplianceGap",
    "RiskAssessment",
    "RemediationAction",
    "RemediationDependency",
    "RemediationScenario",
    "ScenarioResult",
    "AgentRun",
    "AgentEvent",
    "AuditEvent",
    "Approval",
]
