"""Pydantic request/response schemas for the v1 API."""
from __future__ import annotations

from datetime import date, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class ORMModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class Page(BaseModel):
    items: list[Any]
    total: int
    page: int
    page_size: int
    pages: int


class PageParams(BaseModel):
    page: int = Field(default=1, ge=1)
    page_size: int = Field(default=25, ge=1, le=200)
    sort: str | None = None
    order: Literal["asc", "desc"] = "desc"


# ---------------------------------------------------------------------------
# Documents
# ---------------------------------------------------------------------------
class DocumentOut(ORMModel):
    id: str
    filename: str
    content_type: str
    size_bytes: int
    source_kind: str
    source_kind_label: str | None = None
    source_url: str | None = None
    publisher: str | None = None
    published_at: date | None = None
    jurisdiction: str | None = None
    document_type: str | None = None
    reference_number: str | None = None
    page_count: int | None = None
    text_length: int
    extraction_status: str
    extraction_error: str | None = None
    ocr_used: bool
    parse_duration_ms: int | None = None
    uploaded_by: str | None = None
    is_archived: bool
    created_at: datetime
    updated_at: datetime


class DocumentVersionOut(ORMModel):
    id: str
    document_id: str
    version_label: str
    sha256: str
    published_at: date | None = None
    effective_from: date | None = None
    text_length: int
    chunk_count: int
    change_summary: str | None = None
    created_by: str | None = None
    created_at: datetime


class DocumentChunkOut(ORMModel):
    id: str
    chunk_index: int
    page: int | None = None
    section: str | None = None
    heading: str | None = None
    char_count: int
    text: str


class DocumentDetail(DocumentOut):
    chunks: list[DocumentChunkOut] = Field(default_factory=list)
    versions: list[DocumentVersionOut] = Field(default_factory=list)


class IngestSampleRequest(BaseModel):
    path: str
    filename: str | None = None
    source_kind: Literal["official", "synthetic", "internal"] = "internal"
    source_url: str | None = None
    publisher: str | None = None
    published_at: date | None = None
    jurisdiction: str | None = None
    reference_number: str | None = None
    version_label: str = "v1"
    actor: str = "system"


class IngestSampleResponse(BaseModel):
    document_id: str
    filename: str
    page_count: int
    text_length: int
    chunk_count: int
    ocr_used: bool
    duplicate_of: str | None = None
    warnings: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Regulations
# ---------------------------------------------------------------------------
class RegulationCreate(BaseModel):
    source_id: str | None = None
    source_name: str | None = None
    code: str = Field(min_length=3, max_length=120)
    title: str = Field(min_length=5, max_length=500)
    category: str | None = None
    regulator: str = "Reserve Bank of India"
    jurisdiction: str = "IN"
    applicability_summary: str | None = None
    source_url: str | None = None


class RegulationOut(ORMModel):
    id: str
    code: str
    title: str
    category: str | None = None
    regulator: str
    jurisdiction: str
    applicability_summary: str | None = None
    status: str
    created_at: datetime


class RegulationVersionCreate(BaseModel):
    version_label: str = Field(min_length=1, max_length=80)
    published_at: date | None = None
    effective_from: date | None = None
    effective_to: date | None = None
    document_id: str | None = None
    supersedes_version_id: str | None = None
    is_current: bool = False
    notes: str | None = None


class RegulationVersionOut(ORMModel):
    id: str
    regulation_id: str
    version_label: str
    published_at: date | None = None
    effective_from: date | None = None
    effective_to: date | None = None
    document_id: str | None = None
    supersedes_version_id: str | None = None
    is_current: bool
    notes: str | None = None
    created_at: datetime


class RegulationDetail(RegulationOut):
    versions: list[RegulationVersionOut] = Field(default_factory=list)


class CompareVersionsRequest(BaseModel):
    from_version_id: str
    to_version_id: str
    actor: str = "diff_engine"
    run_workflow: bool = False


class ChangeOut(ORMModel):
    id: str
    change_uid: str
    change_type: str
    clause_ref: str | None = None
    summary: str
    added_text: str | None = None
    removed_text: str | None = None
    source_reference: str | None = None
    confidence: float
    requires_human_review: bool
    review_status: str
    is_synthetic: bool
    created_at: datetime


class CompareVersionsResponse(BaseModel):
    comparison_id: str
    left_label: str
    right_label: str
    provisions_left: int
    provisions_right: int
    changes: list[ChangeOut]
    summary: dict[str, Any]
    workflow: dict[str, Any] | None = None


# ---------------------------------------------------------------------------
# Obligations
# ---------------------------------------------------------------------------
class ObligationOut(ORMModel):
    id: str
    obligation_uid: str
    regulation_id: str
    regulation_version_id: str
    source_document_id: str | None = None
    source_page: int | None = None
    source_section: str | None = None
    quoted_text: str
    obligation_text: str
    responsible_party: str | None = None
    action_required: str | None = None
    applicability_conditions: list[str] = Field(default_factory=list)
    effective_date: date | None = None
    deadline_date: date | None = None
    deadline_text: str | None = None
    jurisdiction: str | None = None
    severity: str
    severity_rationale: str | None = None
    extraction_confidence: float | None = None
    extraction_method: str
    extraction_model: str | None = None
    review_status: str
    reviewed_by: str | None = None
    reviewed_at: datetime | None = None
    is_synthetic: bool
    created_at: datetime

    @field_validator("applicability_conditions", mode="before")
    @classmethod
    def _decode(cls, v: Any) -> Any:
        if v is None:
            return []
        if isinstance(v, str):
            import json

            try:
                return json.loads(v)
            except (TypeError, ValueError):
                return []
        return v


class ObligationDetail(ObligationOut):
    review_notes: str | None = None
    mappings: list["MappingOut"] = Field(default_factory=list)
    applicability_assessments: list[dict[str, Any]] = Field(default_factory=list)


class ObligationApproveRequest(BaseModel):
    decision: Literal["approved", "rejected", "needs_revision"]
    reviewer: str
    notes: str | None = None


class ExtractObligationsRequest(BaseModel):
    regulation_version_id: str
    document_id: str
    use_llm: bool = False
    limit: int | None = Field(default=None, ge=1, le=2000)
    chunk_ids: list[str] | None = None
    actor: str = "obligation_extraction_agent"
    run_workflow: bool = False


# ---------------------------------------------------------------------------
# Controls
# ---------------------------------------------------------------------------
class ControlCreate(BaseModel):
    control_uid: str | None = None
    title: str = Field(min_length=3, max_length=400)
    policy_version_id: str | None = None
    business_unit_id: str | None = None
    control_objective: str | None = None
    control_activity: str | None = None
    owner_role: str | None = None
    frequency: str | None = None
    control_type: Literal["preventive", "detective", "corrective", "unknown"] = "unknown"
    automation: Literal["manual", "automated", "hybrid", "unknown"] = "unknown"
    expected_evidence: str | None = None
    testing_procedure: str | None = None
    source_document_id: str | None = None
    is_synthetic: bool = False


class ControlOut(ORMModel):
    id: str
    control_uid: str
    title: str
    policy_version_id: str | None = None
    business_unit_id: str | None = None
    control_objective: str | None = None
    control_activity: str | None = None
    owner_role: str | None = None
    frequency: str | None = None
    control_type: str
    automation: str
    expected_evidence: str | None = None
    testing_procedure: str | None = None
    design_effectiveness: str
    operating_effectiveness: str
    design_rationale: str | None = None
    operating_rationale: str | None = None
    last_tested_at: datetime | None = None
    status: str
    is_synthetic: bool
    created_at: datetime


class ControlDetail(ControlOut):
    evidence: list["EvidenceOut"] = Field(default_factory=list)
    tests: list["ControlTestOut"] = Field(default_factory=list)
    mappings: list["MappingOut"] = Field(default_factory=list)
    evidence_summary: dict[str, Any] = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# Mappings
# ---------------------------------------------------------------------------
class MappingOut(ORMModel):
    id: str
    obligation_id: str
    control_id: str
    mapping_rationale: str
    confidence: float
    status: str
    proposed_by: str
    proposed_model: str | None = None
    reviewer_decision: str | None = None
    reviewed_by: str | None = None
    reviewed_at: datetime | None = None
    reject_reason: str | None = None
    created_at: datetime


class MappingDecisionRequest(BaseModel):
    decision: Literal["approved", "rejected"]
    reviewer: str
    reason: str | None = None


class SuggestMappingsRequest(BaseModel):
    obligation_ids: list[str] | None = None
    use_vectors: bool = True
    use_ai: bool = False  # opt-in Cerebras re-rank; default False protects credits
    business_unit_id: str | None = None
    persist: bool = True
    actor: str = "control_mapping_agent"
    run_workflow: bool = False


class MappingCandidateOut(BaseModel):
    control_id: str
    confidence: float
    rationale: str
    lexical_score: float
    vector_score: float
    shared_terms: list[str]
    proposed_by: str
    proposed_model: str | None = None


class SuggestMappingsResponse(BaseModel):
    results: dict[str, list[MappingCandidateOut]]
    persisted: int
    workflow: dict[str, Any] | None = None
    warnings: list[str] = Field(default_factory=list)
    execution_mode: str = "deterministic"
    provider: str | None = None


# ---------------------------------------------------------------------------
# Evidence & tests
# ---------------------------------------------------------------------------
class EvidenceCreate(BaseModel):
    title: str = Field(min_length=3, max_length=400)
    control_ids: list[str] = Field(default_factory=list)
    description: str | None = None
    evidence_period_start: date | None = None
    evidence_period_end: date | None = None
    source: str | None = None
    collection_date: date | None = None
    file_id: str | None = None
    is_synthetic: bool = False
    uploaded_by: str = "compliance.officer"


class EvidenceOut(ORMModel):
    id: str
    evidence_uid: str
    title: str
    file_id: str | None = None
    description: str | None = None
    evidence_period_start: date | None = None
    evidence_period_end: date | None = None
    source: str | None = None
    collection_date: date | None = None
    validity_status: str
    validity_rationale: str | None = None
    freshness_status: str
    review_status: str
    test_result: str | None = None
    is_synthetic: bool
    created_at: datetime


class EvidenceLinkRequest(BaseModel):
    control_ids: list[str]
    link_type: Literal["supports", "contradicts", "partial"] = "supports"
    notes: str | None = None
    actor: str = "compliance.officer"


class ControlTestOut(ORMModel):
    id: str
    test_uid: str
    control_id: str
    test_type: str
    test_procedure: str
    performed_by: str | None = None
    performed_at: datetime | None = None
    result: str
    result_rationale: str | None = None
    exceptions_found: int
    is_synthetic: bool
    created_at: datetime


class RunTestsRequest(BaseModel):
    control_ids: list[str] | None = None
    test_type: Literal["design", "operating", "both"] = "both"
    actor: str = "control_testing_agent"


class RunTestsResponse(BaseModel):
    tests_run: int
    results: dict[str, int]
    tests: list[ControlTestOut]


# ---------------------------------------------------------------------------
# Assessment / gaps
# ---------------------------------------------------------------------------
class AssessmentRunRequest(BaseModel):
    obligation_ids: list[str] | None = None
    actor: str = "gap_analysis_agent"
    run_workflow: bool = False


class GapOut(ORMModel):
    id: str
    gap_uid: str
    obligation_id: str | None = None
    control_id: str | None = None
    business_unit_id: str | None = None
    gap_type: str
    title: str
    description: str
    detection_basis: str
    detected_by: str
    status: str
    severity_hint: str
    truth_status: str
    created_at: datetime


class GapDetail(GapOut):
    detection_context: dict[str, Any] = Field(default_factory=dict)
    closure_evidence: str | None = None
    accepted_risk_reason: str | None = None
    risk_assessments: list["RiskAssessmentOut"] = Field(default_factory=list)
    remediation_actions: list["RemediationActionOut"] = Field(default_factory=list)
    traceability: dict[str, Any] = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# Risk
# ---------------------------------------------------------------------------
class RiskAssessmentOut(ORMModel):
    id: str
    gap_id: str
    regulatory_severity: float
    failure_likelihood: float
    exposure: float
    urgency: float
    evidence_confidence: float
    existing_mitigation: float
    weights: dict[str, float]
    score: float
    score_band: str
    hard_deadline: date | None = None
    days_to_deadline: int | None = None
    deadline_breached: bool
    hard_constraint_flag: str | None = None
    explanation: dict[str, Any]
    calibrated: bool
    calculated_at: datetime
    calculated_by: str

    @field_validator("weights", mode="before")
    @classmethod
    def _decode_weights(cls, v: Any) -> Any:
        if v is None:
            return {}
        if isinstance(v, str):
            import json

            try:
                return json.loads(v)
            except (TypeError, ValueError):
                return {}
        return v

    @field_validator("explanation", mode="before")
    @classmethod
    def _decode_explanation(cls, v: Any) -> Any:
        if v is None:
            return {}
        if isinstance(v, str):
            import json

            try:
                return json.loads(v)
            except (TypeError, ValueError):
                return {"narrative": v}
        return v


class RiskRecalculateRequest(BaseModel):
    gap_ids: list[str] | None = None
    weights: dict[str, float] | None = None
    actor: str = "risk_assessment_agent"


class RiskSummary(BaseModel):
    total_assessed: int
    bands: dict[str, int]
    max_score: float | None = None
    mean_score: float | None = None
    weights_in_use: dict[str, float]
    calibrated: bool = False


# ---------------------------------------------------------------------------
# Remediation
# ---------------------------------------------------------------------------
class RemediationActionOut(ORMModel):
    id: str
    action_uid: str
    gap_id: str
    proposed_action: str
    reason: str
    expected_risk_reduction: float
    estimated_cost_inr: float | None = None
    cost_is_estimate: bool
    estimated_duration_days: int | None = None
    duration_is_estimate: bool
    suggested_owner_role: str | None = None
    deadline: date | None = None
    verification_procedure: str | None = None
    required_evidence_for_closure: str | None = None
    approval_required: bool
    status: str
    approved_by: str | None = None
    approved_at: datetime | None = None
    rejected_reason: str | None = None
    completed_at: datetime | None = None
    closure_evidence: str | None = None
    uncertainty_notes: str | None = None
    required_capability: str | None = None
    created_at: datetime


class RemediationDecisionRequest(BaseModel):
    decision: Literal["approved", "rejected"]
    approver: str
    reason: str | None = None


class RemediationCompleteRequest(BaseModel):
    closure_evidence: str
    completed_by: str
    gap_status: Literal["closed", "in_remediation"] = "closed"


class RemediationGenerateRequest(BaseModel):
    gap_ids: list[str] | None = None
    use_ai: bool = False  # opt-in Cerebras drafts; default False protects credits
    actor: str = "remediation_planning_agent"
    run_workflow: bool = False


# ---------------------------------------------------------------------------
# Impact
# ---------------------------------------------------------------------------
class ImpactAnalyzeRequest(BaseModel):
    change_id: str
    actor: str = "regulatory_impact_agent"
    recalculate_risk: bool = True


class ImpactOut(ORMModel):
    id: str
    impact_uid: str
    change_id: str
    impact_score: float
    priority_delta: float
    narrative: str
    affected_obligation_ids: list[str] = Field(default_factory=list)
    affected_control_ids: list[str] = Field(default_factory=list)
    affected_gap_ids: list[str] = Field(default_factory=list)
    affected_action_ids: list[str] = Field(default_factory=list)
    affected_mapping_ids: list[str] = Field(default_factory=list)
    uncertainty_flags: list[str] = Field(default_factory=list)
    propagation_path: list[dict[str, Any]] = Field(default_factory=list)
    status: str
    created_by: str
    created_at: datetime

    @field_validator(
        "affected_obligation_ids",
        "affected_control_ids",
        "affected_gap_ids",
        "affected_action_ids",
        "affected_mapping_ids",
        "uncertainty_flags",
        mode="before",
    )
    @classmethod
    def _decode_list(cls, v: Any) -> Any:
        if v is None:
            return []
        if isinstance(v, str):
            import json

            try:
                return json.loads(v)
            except (TypeError, ValueError):
                return []
        return v

    @field_validator("propagation_path", mode="before")
    @classmethod
    def _decode_propagation(cls, v: Any) -> Any:
        if v is None:
            return []
        if isinstance(v, str):
            import json

            try:
                return json.loads(v)
            except (TypeError, ValueError):
                return []
        return v


# ---------------------------------------------------------------------------
# Scenarios
# ---------------------------------------------------------------------------
class ScenarioCreate(BaseModel):
    name: str = Field(min_length=3, max_length=255)
    strategy: Literal["max_risk_reduction", "deadline_first", "shared_control", "custom"] = "max_risk_reduction"
    description: str | None = None
    budget_inr: float | None = Field(default=None, ge=0)
    capacity_days: int | None = Field(default=None, ge=0)
    horizon_days: int | None = Field(default=None, ge=1)
    action_ids: list[str] | None = None
    gap_ids: list[str] | None = None
    team_capacity: dict[str, float] | None = None
    assumptions: str | None = None
    created_by: str = "compliance.officer"


class ScenarioOut(ORMModel):
    id: str
    scenario_uid: str
    name: str
    strategy: str
    description: str | None = None
    budget_inr: float | None = None
    capacity_days: int | None = None
    horizon_days: int | None = None
    created_by: str | None = None
    created_at: datetime


class ScenarioResultOut(ORMModel):
    id: str
    scenario_id: str
    selected_actions: list[dict[str, Any]]
    rejected_actions: list[dict[str, Any]]
    constraint_checks: list[dict[str, Any]]
    objective_value: float
    projected_risk_reduction: float
    projected_cost_inr: float
    projected_duration_days: float
    unresolved_gap_ids: list[str]
    solver: str
    optimality_claim: str
    explanation: dict[str, Any]
    assumptions: list[str]
    is_prediction: bool
    created_at: datetime

    @field_validator(
        "selected_actions",
        "rejected_actions",
        "constraint_checks",
        "unresolved_gap_ids",
        "assumptions",
        mode="before",
    )
    @classmethod
    def _decode_list(cls, v: Any) -> Any:
        if v is None:
            return []
        if isinstance(v, str):
            import json

            try:
                return json.loads(v)
            except (TypeError, ValueError):
                return []
        return v

    @field_validator("explanation", mode="before")
    @classmethod
    def _decode_explanation(cls, v: Any) -> Any:
        if v is None:
            return {}
        if isinstance(v, str):
            import json

            try:
                return json.loads(v)
            except (TypeError, ValueError):
                return {}
        return v


# ---------------------------------------------------------------------------
# Audit / agents
# ---------------------------------------------------------------------------
class AuditEventOut(ORMModel):
    id: str
    event_uid: str
    actor: str
    actor_role: str | None = None
    action: str
    entity_type: str
    entity_id: str | None = None
    entity_uid: str | None = None
    correlation_id: str | None = None
    run_uid: str | None = None
    before: str | None = None
    after: str | None = None
    request_path: str | None = None
    request_method: str | None = None
    detail: str | None = None
    created_at: datetime


class AgentEventOut(ORMModel):
    id: str
    run_id: str
    agent_name: str
    sequence: int
    status: str
    attempt: int
    retries: int
    duration_ms: int | None = None
    message: str | None = None
    error: str | None = None
    model_used: str | None = None
    rule_used: str | None = None
    warnings: list[str] = Field(default_factory=list)
    citations: list[dict[str, Any]] = Field(default_factory=list)
    created_at: datetime

    @field_validator("warnings", "citations", mode="before")
    @classmethod
    def _decode(cls, v: Any) -> Any:
        if v is None:
            return []
        if isinstance(v, str):
            import json

            try:
                return json.loads(v)
            except (TypeError, ValueError):
                return []
        return v


class AgentRunOut(ORMModel):
    id: str
    run_uid: str
    workflow: str
    correlation_id: str
    orchestration_backend: str
    deterministic_mode: bool
    status: str
    triggered_by: str | None = None
    started_at: datetime
    finished_at: datetime | None = None
    duration_ms: int | None = None
    error: str | None = None
    events: list[AgentEventOut] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# System
# ---------------------------------------------------------------------------
class HealthOut(BaseModel):
    status: str
    app: str
    version: str
    tagline: str
    timestamp: datetime


class SystemStatusOut(BaseModel):
    app: str
    version: str
    environment: str
    mode: str
    database: dict[str, Any]
    llm: dict[str, Any]
    embeddings: dict[str, Any]
    ocr: dict[str, Any]
    retrieval: dict[str, Any]
    orchestration: dict[str, Any]
    optimiser: dict[str, Any]
    counts: dict[str, int]
    warnings: list[str] = Field(default_factory=list)
    # --- hosted-AI observability (Gemini + Cerebras + deterministic) ---
    providers: dict[str, Any] | None = None
    execution_modes: dict[str, Any] | None = None


class WorkflowRunRequest(BaseModel):
    workflow: str
    params: dict[str, Any] = Field(default_factory=dict)
    actor: str = "compliance.officer"


class WorkflowRunResponse(BaseModel):
    run_uid: str
    workflow: str
    status: str
    steps_completed: list[str]
    failures: list[dict[str, Any]]
    outputs: dict[str, Any]


# ---------------------------------------------------------------------------
# Analysis
# ---------------------------------------------------------------------------
class ContradictionOut(BaseModel):
    kind: str
    confidence: float
    left_ref: dict[str, Any]
    right_ref: dict[str, Any]
    left_text: str
    right_text: str
    explanation: str
    possible_explanation: str
    resolution_status: str


class CrossRegulationOut(BaseModel):
    similar_pairs: list[dict[str, Any]]
    shared_controls: list[dict[str, Any]]
    overlapping_requirements: list[dict[str, Any]]
    reuse_opportunities: list[dict[str, Any]]
    deadline_conflicts: list[dict[str, Any]]
    distinct_but_similar: list[dict[str, Any]]
    note: str


class DashboardOut(BaseModel):
    counts: dict[str, int]
    risk: dict[str, Any]
    evidence: dict[str, Any]
    recent_activity: list[dict[str, Any]]
    open_gaps_by_type: dict[str, int]
    pending_reviews: dict[str, int]
    demo_ready: bool


ObligationDetail.model_rebuild()
ControlDetail.model_rebuild()
GapDetail.model_rebuild()
