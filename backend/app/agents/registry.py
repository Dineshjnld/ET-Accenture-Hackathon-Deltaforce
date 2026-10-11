"""The 13 logical compliance agents.

Each agent is a thin wrapper around a service in :mod:`app.services` plus a
strict declaration of purpose, tools, timeout and failure behaviour. The
heavy lifting lives in the services so it can be tested directly; the agents
exist to bound execution, persist structured events and enforce the audit
trail.
"""
from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.agents.base import AgentInput, AgentOutput, BaseAgent, ToolSet
from app.config import settings
from app.logging_config import get_logger
from app.models.core import enum_value

log = get_logger("regulens.agents.registry")


class IngestionAgent(BaseAgent):
    """Feature 1 — regulatory intelligence and knowledge ingestion."""

    name = "ingestion_agent"
    purpose = "Parse and store regulatory and internal documents with full provenance."
    permitted_tools = ("parse_pdf", "parse_docx", "parse_text", "ocr_pdf")
    timeout_seconds = 300
    max_retries = 1

    def __init__(self, session: Session, tools: ToolSet | None = None) -> None:
        super().__init__(tools or ToolSet(self.permitted_tools))
        self.session = session

    def execute(self, payload: AgentInput) -> AgentOutput:
        from app.models.regulatory import Document
        from app.services.ingestion import ingest_path

        params = payload.params
        if not params.get("path"):
            return AgentOutput(ok=False, summary="No path supplied", error="missing_param:path")
        result = ingest_path(
            self.session,
            path=params["path"],
            original_filename=params.get("filename"),
            content_type=params.get("content_type"),
            actor=payload.actor,
            source_kind=params.get("source_kind", "internal"),
        )
        doc = self.session.get(Document, result.document_id)
        return AgentOutput(
            summary=f"Ingested {result.filename}: {result.chunk_count} chunks, {result.text_length} chars",
            data={
                "document_id": result.document_id,
                "ocr_used": result.ocr_used,
                "duplicate_of": result.duplicate_of,
                "source_kind": str(doc.source_kind) if doc else None,
            },
            warnings=result.warnings,
            citations=[{"type": "document", "id": result.document_id, "filename": result.filename}],
            rule_used="app.parsing.parse_document",
            deterministic=True,
        )


class RegulatoryIntelligenceAgent(BaseAgent):
    """Feature 2 — regulatory change intelligence."""

    name = "regulatory_intelligence_agent"
    purpose = "Register regulation versions and detect candidate changes between them."
    permitted_tools = ("compare_versions", "read_document_text")
    timeout_seconds = 600

    def __init__(self, session: Session, tools: ToolSet | None = None) -> None:
        super().__init__(tools or ToolSet(self.permitted_tools))
        self.session = session

    def execute(self, payload: AgentInput) -> AgentOutput:
        from app.services.diffing import compare_versions

        p = payload.params
        result, changes = compare_versions(
            self.session,
            regulation_id=p["regulation_id"],
            from_version_id=p["from_version_id"],
            to_version_id=p["to_version_id"],
            actor=payload.actor,
            correlation_id=payload.correlation_id,
        )
        return AgentOutput(
            summary=(
                f"Compared {result.left_label} → {result.right_label}: "
                f"{len(changes)} candidate change(s), "
                f"{result.summary['requires_human_review']} requiring human review"
            ),
            data={
                "changes": [
                    {"id": c.id, "type": str(c.change_type), "confidence": c.confidence, "clause": c.clause_ref}
                    for c in changes
                ],
                "summary": result.summary,
            },
            citations=[{"type": "regulation_version", "id": result.right_version_id}],
            rule_used="app.services.diffing.compare_texts",
            deterministic=True,
        )


class ObligationExtractionAgent(BaseAgent):
    """Feature 3 — regulatory obligation extraction."""

    name = "obligation_extraction_agent"
    purpose = "Extract atomic obligations with verbatim source citations."
    permitted_tools = ("extract_deterministic", "extract_llm", "read_document_text")
    timeout_seconds = 900

    def __init__(self, session: Session, tools: ToolSet | None = None) -> None:
        super().__init__(tools or ToolSet(self.permitted_tools))
        self.session = session

    def execute(self, payload: AgentInput) -> AgentOutput:
        from app.models.regulatory import Document, RegulationVersion
        from app.services.obligations import persist_obligations

        p = payload.params
        version = self.session.get(RegulationVersion, p["regulation_version_id"])
        if version is None:
            return AgentOutput(ok=False, summary="Unknown regulation version", error="not_found")
        document = self.session.get(Document, p["document_id"])
        if document is None:
            return AgentOutput(ok=False, summary="Unknown document", error="not_found")

        warnings: list[str] = []
        model_used = None
        method = "deterministic"
        execution_mode = "deterministic"
        provider_used: str | None = None
        ai_created_ids: list[str] = []
        if bool(p.get("use_llm")):
            from app.config import settings as _settings
            from app.llm.router import get_router as _get_router

            if _settings.effective_deterministic:
                warnings.append(
                    "LLM extraction was requested but no hosted provider is available. "
                    "Falling back to deterministic extraction — no model output is claimed."
                )
                execution_mode = "deterministic_fallback"
            else:
                _router = _get_router()
                _preferred = _router.provider_for_task("obligation_extraction")
                if not _router._provider(_preferred).available():
                    warnings.append(
                        f"LLM extraction was requested but '{_preferred}' is not available. "
                        "Falling back to deterministic extraction — no model output is claimed."
                    )
                    execution_mode = "deterministic_fallback"
                else:
                    from app.models.regulatory import DocumentChunk
                    from sqlalchemy import select as _select

                    from app.services.ai_assist import extract_obligations_ai
                    from app.services.obligations import persist_ai_obligations as _persist_ai

                    _chunks = list(
                        self.session.scalars(
                            _select(DocumentChunk)
                            .where(DocumentChunk.document_id == document.id)
                            .order_by(DocumentChunk.chunk_index)
                            .limit(3)
                        )
                    )
                    _sample = "\n\n".join(getattr(c, "text", "") for c in _chunks)[:12000]
                    _ai = extract_obligations_ai(_sample or document.filename, document.filename)
                    warnings.extend(_ai["warnings"])
                    if _ai["ok"] and _ai["parsed"] is not None:
                        try:
                            _created_ai, _ai_w = _persist_ai(
                                self.session,
                                list(_ai["parsed"].obligations or []),
                                regulation_version=version,
                                document=document,
                                provider=_ai["provider"],
                                model=_ai["model"],
                                source_text=_sample,
                            )
                            warnings.extend(_ai_w)
                            ai_created_ids = [o.id for o in _created_ai]
                            method = _ai["provider"]
                            execution_mode = _ai["execution_mode"]
                            provider_used = _ai["provider"]
                            model_used = _ai["model"]
                        except Exception as exc:  # noqa: BLE001
                            warnings.append(f"AI candidates rejected ({str(exc)[:160]}).")
                            execution_mode = "deterministic_fallback"
                    else:
                        execution_mode = "deterministic_fallback"

        created = persist_obligations(
            self.session,
            regulation_version=version,
            document=document,
            extraction_method="deterministic",
            extraction_model=None,
            limit=p.get("limit"),
            chunk_ids=p.get("chunk_ids"),
        )
        if not ai_created_ids and execution_mode in ("gemini", "cerebras", "cache"):
            execution_mode = "deterministic"
            method = "deterministic"
        return AgentOutput(
            summary=f"Extracted {len(created) + len(ai_created_ids)} obligation candidate(s) using {method} extraction",
            data={
                "count": len(created) + len(ai_created_ids),
                "method": method,
                "execution_mode": execution_mode,
                "provider": provider_used,
                "obligation_ids": [*ai_created_ids, *(o.id for o in created)],
                "review_status": "pending",
            },
            citations=[
                {
                    "document_id": document.id,
                    "page": o.source_page,
                    "section": o.source_section,
                    "quoted_text": o.quoted_text[:300],
                }
                for o in created[:20]
            ],
            warnings=warnings,
            model_used=model_used,
            rule_used="app.services.obligations.persist_obligations",
            deterministic=method == "deterministic",
        )


class ApplicabilityAgent(BaseAgent):
    """Feature 3/4 — applicability determination."""

    name = "applicability_agent"
    purpose = "Determine whether an obligation applies to an organisation using explicit rules."
    permitted_tools = ("evaluate_applicability_rules", "read_organization_profile")
    timeout_seconds = 300

    def __init__(self, session: Session, tools: ToolSet | None = None) -> None:
        super().__init__(tools or ToolSet(self.permitted_tools))
        self.session = session

    def execute(self, payload: AgentInput) -> AgentOutput:
        from app.models.identity import BusinessUnit, Organization
        from app.models.obligations import Obligation
        from app.services.applicability import assess_obligation

        p = payload.params
        org = self.session.get(Organization, p["organization_id"])
        if org is None:
            return AgentOutput(ok=False, summary="Unknown organization", error="not_found")
        bu = self.session.get(BusinessUnit, p["business_unit_id"]) if p.get("business_unit_id") else None
        obligation_ids = p.get("obligation_ids")
        stmt = self.session.query(Obligation).filter(Obligation.review_status == "approved")
        if obligation_ids:
            stmt = stmt.filter(Obligation.id.in_(obligation_ids))
        results = []
        warnings: list[str] = []
        for obligation in stmt.all():
            outcome = assess_obligation(self.session, obligation, organization=org, business_unit=bu)
            results.append(
                {
                    "obligation_id": obligation.id,
                    "obligation_uid": obligation.obligation_uid,
                    "applicable": outcome.applicable,
                    "rationale": outcome.rationale,
                    "missing_attributes": outcome.missing_attributes,
                    "rule": outcome.rule_name,
                }
            )
            if outcome.applicable == "unknown":
                warnings.append(
                    f"{obligation.obligation_uid}: applicability unknown ({', '.join(outcome.missing_attributes)})"
                )
        return AgentOutput(
            summary=f"Assessed {len(results)} obligation(s): "
            f"{sum(1 for r in results if r['applicable'] == 'yes')} applicable, "
            f"{sum(1 for r in results if r['applicable'] == 'unknown')} unknown",
            data={"assessments": results},
            warnings=warnings,
            rule_used="app.services.applicability.evaluate_rule",
            deterministic=True,
        )


class ControlMappingAgent(BaseAgent):
    """Feature 5 — regulatory-to-control mapping."""

    name = "control_mapping_agent"
    purpose = "Propose obligation→control candidates using hybrid retrieval plus explicit rules."
    permitted_tools = ("suggest_mappings", "embed_text", "lexical_search")
    timeout_seconds = 600

    def __init__(self, session: Session, tools: ToolSet | None = None) -> None:
        super().__init__(tools or ToolSet(self.permitted_tools))
        self.session = session

    def execute(self, payload: AgentInput) -> AgentOutput:
        from app.models.obligations import Obligation
        from app.services.mapping import persist_candidates, suggest_mappings

        p = payload.params
        obligation_ids = p.get("obligation_ids") or [
            o.id
            for o in self.session.query(Obligation).filter(Obligation.review_status == "approved").limit(200)
        ]
        created = 0
        citations: list[dict[str, Any]] = []
        for obligation_id in obligation_ids:
            obligation = self.session.get(Obligation, obligation_id)
            if obligation is None:
                continue
            candidates = suggest_mappings(
                self.session, obligation, use_vectors=bool(p.get("use_vectors", True))
            )
            created += len(persist_candidates(self.session, obligation, candidates))
            for cand in candidates:
                citations.append(
                    {
                        "type": "mapping_candidate",
                        "obligation_id": obligation.id,
                        "control_id": cand.control_id,
                        "confidence": cand.confidence,
                        "shared_terms": cand.shared_terms,
                    }
                )
        return AgentOutput(
            summary=f"Created {created} candidate mapping(s) for human review",
            data={"candidates": created},
            citations=citations[:50],
            warnings=[
                "Candidate mappings are NOT approved mappings. A reviewer must approve or reject each one."
            ],
            rule_used="app.services.mapping.suggest_mappings",
        )


class EvidenceAssessmentAgent(BaseAgent):
    """Feature 7 — evidence-based compliance assessment."""

    name = "evidence_assessment_agent"
    purpose = "Assess evidence sufficiency, freshness and review status for controls."
    permitted_tools = ("assess_evidence", "list_control_evidence")
    timeout_seconds = 300

    def __init__(self, session: Session, tools: ToolSet | None = None) -> None:
        super().__init__(tools or ToolSet(self.permitted_tools))
        self.session = session

    def execute(self, payload: AgentInput) -> AgentOutput:
        from app.models.controls import Control
        from app.models.evidence import Evidence, EvidenceControlLink
        from app.services.assessment import assess_evidence

        controls = self.session.query(Control).all()
        updated = 0
        states: dict[str, int] = {}
        for control in controls:
            links = [
                l
                for l in self.session.query(EvidenceControlLink)
                .filter(EvidenceControlLink.control_id == control.id)
                .all()
            ]
            for link in links:
                evidence = self.session.get(Evidence, link.evidence_id)
                if evidence is None:
                    continue
                result = assess_evidence(evidence)
                evidence.validity_status = result.validity
                evidence.validity_rationale = result.rationale
                evidence.freshness_status = result.freshness
                updated += 1
                key = result.validity.value
                states[key] = states.get(key, 0) + 1
        return AgentOutput(
            summary=f"Re-assessed {updated} evidence artefact(s): {states}",
            data={"updated": updated, "states": states},
            rule_used="app.services.assessment.assess_evidence",
            deterministic=True,
        )


class ControlTestingAgent(BaseAgent):
    """Feature 6 — control effectiveness assessment (design and operating)."""

    name = "control_testing_agent"
    purpose = "Execute design and operating effectiveness tests for controls."
    permitted_tools = ("run_design_test", "run_operating_test", "assess_design")
    timeout_seconds = 600

    def __init__(self, session: Session, tools: ToolSet | None = None) -> None:
        super().__init__(tools or ToolSet(self.permitted_tools))
        self.session = session

    def execute(self, payload: AgentInput) -> AgentOutput:
        from app.models.controls import Control
        from app.services.assessment import run_design_test, run_operating_test

        stmt = self.session.query(Control)
        if payload.params.get("control_ids"):
            stmt = stmt.filter(Control.id.in_(payload.params["control_ids"]))
        controls = stmt.all()
        run = 0
        results: dict[str, int] = {}
        for control in controls:
            design = run_design_test(self.session, control, actor=payload.actor)
            results[str(design.result)] = results.get(str(design.result), 0) + 1
            run += 1
            if payload.params.get("operating", True):
                operating = run_operating_test(self.session, control, actor=payload.actor)
                results[str(operating.result)] = results.get(str(operating.result), 0) + 1
                run += 1
        return AgentOutput(
            summary=f"Executed {run} control test(s): {results}",
            data={"tests_executed": run, "results": results},
            rule_used="app.services.assessment.run_design_test/run_operating_test",
            deterministic=True,
        )


class GapAnalysisAgent(BaseAgent):
    """Feature 8 — gap identification."""

    name = "gap_analysis_agent"
    purpose = "Detect compliance gaps from obligations, mappings, controls, evidence and tests."
    permitted_tools = ("detect_gaps", "assess_operating")
    timeout_seconds = 600

    def __init__(self, session: Session, tools: ToolSet | None = None) -> None:
        super().__init__(tools or ToolSet(self.permitted_tools))
        self.session = session

    def execute(self, payload: AgentInput) -> AgentOutput:
        from app.services.gaps import detect_gaps

        created = detect_gaps(self.session, obligation_ids=payload.params.get("obligation_ids"))
        by_type: dict[str, int] = {}
        for gap in created:
            by_type[enum_value(gap.gap_type)] = by_type.get(enum_value(gap.gap_type), 0) + 1
        return AgentOutput(
            summary=f"Detected {len(created)} new gap(s): {by_type}",
            data={"gaps": [g.id for g in created], "by_type": by_type},
            rule_used="app.services.gaps.detect_gaps",
            deterministic=True,
        )


class RiskAssessmentAgent(BaseAgent):
    """Feature 9 — risk-based gap prioritisation."""

    name = "risk_assessment_agent"
    purpose = "Score every open gap with a transparent, configurable weighted model."
    permitted_tools = ("score_gap", "read_risk_weights")
    timeout_seconds = 300

    def __init__(self, session: Session, tools: ToolSet | None = None) -> None:
        super().__init__(tools or ToolSet(self.permitted_tools))
        self.session = session

    def execute(self, payload: AgentInput) -> AgentOutput:
        from app.models.remediation import ComplianceGap, RiskAssessment
        from app.services.risk import score_gap

        weights = payload.params.get("weights") or settings.RISK_WEIGHTS
        gaps = self.session.query(ComplianceGap).filter(ComplianceGap.status.in_(["open", "in_remediation"])).all()
        scored = []
        for gap in gaps:
            assessment, explanation = score_gap(self.session, gap, weights=weights, actor=payload.actor)
            scored.append(
                {
                    "gap_uid": gap.gap_uid,
                    "score": assessment.score,
                    "band": assessment.score_band,
                    "hard_constraint": assessment.hard_constraint_flag,
                }
            )
        return AgentOutput(
            summary=f"Scored {len(scored)} gap(s)",
            data={"assessments": scored},
            rule_used="app.services.risk.score_gap",
            deterministic=True,
        )


class RemediationPlanningAgent(BaseAgent):
    """Feature 10 — remediation recommendation engine."""

    name = "remediation_planning_agent"
    purpose = "Generate actionable remediation recommendations for open gaps."
    permitted_tools = ("generate_remediation", "set_dependencies")
    timeout_seconds = 300

    def __init__(self, session: Session, tools: ToolSet | None = None) -> None:
        super().__init__(tools or ToolSet(self.permitted_tools))
        self.session = session

    def execute(self, payload: AgentInput) -> AgentOutput:
        from app.services.remediation import generate_for_gaps

        created = generate_for_gaps(self.session, gap_ids=payload.params.get("gap_ids"))
        unknown_cost = sum(1 for a in created if a.estimated_cost_inr is None)
        unknown_duration = sum(1 for a in created if a.estimated_duration_days is None)
        return AgentOutput(
            summary=f"Generated {len(created)} remediation recommendation(s)",
            data={
                "actions": [a.id for a in created],
                "unknown_cost": unknown_cost,
                "unknown_duration": unknown_duration,
            },
            warnings=(
                [f"{unknown_cost} recommendation(s) have no cost estimate — left unknown, not fabricated."]
                if unknown_cost
                else []
            ),
            rule_used="app.services.remediation.generate_for_gaps",
            deterministic=True,
        )


class RegulatoryChangeImpactAgent(BaseAgent):
    """Feature 11 — autonomous regulatory impact analysis."""

    name = "regulatory_impact_agent"
    purpose = "Propagate a regulatory change through obligations, controls, evidence, gaps and remediation."
    permitted_tools = ("analyze_impact", "recalculate_risk")
    timeout_seconds = 600

    def __init__(self, session: Session, tools: ToolSet | None = None) -> None:
        super().__init__(tools or ToolSet(self.permitted_tools))
        self.session = session

    def execute(self, payload: AgentInput) -> AgentOutput:
        from app.services.impact import analyze_impact

        result = analyze_impact(
            self.session, payload.params["change_id"], actor=payload.actor
        )
        return AgentOutput(
            summary=(
                f"Impact {result.impact.impact_uid}: {len(result.affected_obligations)} obligation(s), "
                f"{len(result.affected_controls)} control(s), {len(result.affected_gaps)} gap(s); "
                f"{len(result.uncertain_relations)} uncertain relation(s)"
            ),
            data={
                "impact_uid": result.impact.impact_uid,
                "score": result.impact.impact_score,
                "affected": {
                    "obligations": result.affected_obligations,
                    "controls": result.affected_controls,
                    "gaps": result.affected_gaps,
                    "actions": result.affected_actions,
                },
            },
            citations=[{"type": "regulatory_change", "id": payload.params["change_id"]}],
            warnings=result.uncertain_relations,
            rule_used="app.services.impact.analyze_impact",
            deterministic=True,
        )


class ScenarioAnalysisAgent(BaseAgent):
    """Feature 12 — what-if and remediation simulation."""

    name = "scenario_analysis_agent"
    purpose = "Optimise a remediation portfolio under budget, capacity, precedence and deadline constraints."
    permitted_tools = ("run_scenario", "load_candidates")
    timeout_seconds = 600

    def __init__(self, session: Session, tools: ToolSet | None = None) -> None:
        super().__init__(tools or ToolSet(self.permitted_tools))
        self.session = session

    def execute(self, payload: AgentInput) -> AgentOutput:
        from app.models.remediation import RemediationScenario
        from app.services.scenarios import run_scenario

        scenario = self.session.get(RemediationScenario, payload.params["scenario_id"])
        if scenario is None:
            return AgentOutput(ok=False, summary="Unknown scenario", error="not_found")
        result = run_scenario(self.session, scenario, actor=payload.actor)
        return AgentOutput(
            summary=(
                f"Scenario {scenario.scenario_uid} solved with {result.solver}: objective "
                f"{result.objective_value}, projected cost {result.projected_cost_inr}, "
                f"{result.projected_duration_days}d, {len(_ids(result.unresolved_gap_ids))} unresolved gap(s)"
            ),
            data={
                "result_id": result.id,
                "solver": result.solver,
                "objective": result.objective_value,
                "optimality_claim": result.optimality_claim,
                "is_prediction": result.is_prediction,
            },
            warnings=["Results are predictions, not observed outcomes."],
            rule_used="app.services.scenarios.run_scenario",
        )


class ValidationAuditAgent(BaseAgent):
    """Cross-cutting — validation, audit integrity and contradictory-claim detection."""

    name = "validation_audit_agent"
    purpose = "Validate agent outputs, verify audit-trail integrity and run contradiction detection."
    permitted_tools = ("validate_schema", "verify_audit_chain", "detect_contradictions", "cross_regulation_report")
    timeout_seconds = 300

    def __init__(self, session: Session, tools: ToolSet | None = None) -> None:
        super().__init__(tools or ToolSet(self.permitted_tools))
        self.session = session

    def execute(self, payload: AgentInput) -> AgentOutput:
        from app.services.contradiction import detect_all, report_to_json
        from app.services.cross_regulation import build_report, report_to_json as cross_json

        report = detect_all(self.session)
        cross = build_report(self.session)
        from app.services.audit import recent_activity

        recent = list(recent_activity(self.session, limit=5))
        return AgentOutput(
            summary=(
                f"Validated state: {len(report.findings)} potential contradiction(s), "
                f"{len(cross.pairs)} similar obligation pair(s), {len(recent)} recent audit event(s)"
            ),
            data={
                "contradictions": report_to_json(report)["findings"][:20],
                "cross_regulation": cross_json(cross)["distinct_but_similar"][:10],
                "audit_events_recent": len(recent),
            },
            rule_used="app.services.contradiction.detect_all",
            deterministic=True,
        )


def _ids(value: Any) -> list[str]:
    import json

    if isinstance(value, str):
        try:
            return json.loads(value)
        except (TypeError, ValueError):
            return []
    return list(value or [])


AGENT_CLASSES = {
    IngestionAgent.name: IngestionAgent,
    RegulatoryIntelligenceAgent.name: RegulatoryIntelligenceAgent,
    ObligationExtractionAgent.name: ObligationExtractionAgent,
    ApplicabilityAgent.name: ApplicabilityAgent,
    ControlMappingAgent.name: ControlMappingAgent,
    EvidenceAssessmentAgent.name: EvidenceAssessmentAgent,
    ControlTestingAgent.name: ControlTestingAgent,
    GapAnalysisAgent.name: GapAnalysisAgent,
    RiskAssessmentAgent.name: RiskAssessmentAgent,
    RemediationPlanningAgent.name: RemediationPlanningAgent,
    RegulatoryChangeImpactAgent.name: RegulatoryChangeImpactAgent,
    ScenarioAnalysisAgent.name: ScenarioAnalysisAgent,
    ValidationAuditAgent.name: ValidationAuditAgent,
}


def agent_catalog() -> list[dict[str, Any]]:
    """Describe all logical agents without instantiating them."""
    return [
        {
            "name": cls.name,
            "purpose": cls.purpose,
            "permitted_tools": list(cls.permitted_tools),
            "timeout_seconds": cls.timeout_seconds,
            "max_retries": cls.max_retries,
        }
        for cls in AGENT_CLASSES.values()
    ]


__all__ = ["AGENT_CLASSES", "agent_catalog"]

