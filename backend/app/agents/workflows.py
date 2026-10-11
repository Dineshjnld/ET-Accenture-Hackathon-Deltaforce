"""Workflow definitions for the bounded, explicit state machine.

Each workflow is a small DAG. There is no unbounded loop: every workflow has a
finite step list and a maximum of ``max_rounds`` executions.
"""
from __future__ import annotations

from app.agents.base import WorkflowDefinition, WorkflowStep

MAX_ROUNDS = 5

WORKFLOWS: dict[str, WorkflowDefinition] = {
    "ingest_and_extract": WorkflowDefinition(
        name="ingest_and_extract",
        description="Ingest a document, extract obligations and assess applicability.",
        steps=[
            WorkflowStep(name="ingest", agent="ingestion_agent", stop_on_failure=True),
            WorkflowStep(name="extract_obligations", agent="obligation_extraction_agent"),
            WorkflowStep(name="assess_applicability", agent="applicability_agent"),
            WorkflowStep(name="validate", agent="validation_audit_agent"),
        ],
    ),
    "compliance_assessment": WorkflowDefinition(
        name="compliance_assessment",
        description="Map obligations to controls, assess evidence, test controls, detect gaps, score risk "
        "and generate remediation.",
        steps=[
            WorkflowStep(name="map_controls", agent="control_mapping_agent"),
            WorkflowStep(name="assess_evidence", agent="evidence_assessment_agent"),
            WorkflowStep(name="test_controls", agent="control_testing_agent"),
            WorkflowStep(name="detect_gaps", agent="gap_analysis_agent", stop_on_failure=False),
            WorkflowStep(name="score_risk", agent="risk_assessment_agent"),
            WorkflowStep(name="plan_remediation", agent="remediation_planning_agent"),
            WorkflowStep(name="validate", agent="validation_audit_agent"),
        ],
    ),
    "change_impact": WorkflowDefinition(
        name="change_impact",
        description="Compare two regulation versions and propagate the impact through the compliance graph.",
        steps=[
            WorkflowStep(name="compare_versions", agent="regulatory_intelligence_agent", stop_on_failure=True),
            WorkflowStep(name="analyze_impact", agent="regulatory_impact_agent"),
            WorkflowStep(name="validate", agent="validation_audit_agent"),
        ],
    ),
    "scenario_planning": WorkflowDefinition(
        name="scenario_planning",
        description="Re-score risk and run a remediation scenario optimisation.",
        steps=[
            WorkflowStep(name="score_risk", agent="risk_assessment_agent"),
            WorkflowStep(name="run_scenario", agent="scenario_analysis_agent"),
            WorkflowStep(name="validate", agent="validation_audit_agent"),
        ],
    ),
    "evidence_review": WorkflowDefinition(
        name="evidence_review",
        description="Re-assess evidence and re-test controls after new evidence is uploaded.",
        steps=[
            WorkflowStep(name="assess_evidence", agent="evidence_assessment_agent"),
            WorkflowStep(name="test_controls", agent="control_testing_agent"),
            WorkflowStep(name="detect_gaps", agent="gap_analysis_agent"),
            WorkflowStep(name="score_risk", agent="risk_assessment_agent"),
        ],
    ),
}


def get_workflow(name: str) -> WorkflowDefinition:
    if name not in WORKFLOWS:
        raise KeyError(
            f"Unknown workflow '{name}'. Available: {', '.join(sorted(WORKFLOWS))}"
        )
    return WORKFLOWS[name]


def list_workflows() -> list[dict[str, object]]:
    return [
        {
            "name": wf.name,
            "description": wf.description,
            "steps": [{"name": s.name, "agent": s.agent, "depends_on": s.depends_on} for s in wf.steps],
        }
        for wf in WORKFLOWS.values()
    ]


__all__ = ["WORKFLOWS", "get_workflow", "list_workflows", "MAX_ROUNDS"]
