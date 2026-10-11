"""Native orchestration backend: an explicit, bounded state machine.

State machine states::

    PENDING -> RUNNING -> (step loop, bounded by MAX_ROUNDS) -> SUCCEEDED
                                                           \\-> PARTIAL
                                                           \\-> FAILED
                                                           \\-> TIMED_OUT

Progress is persisted after every step in ``agent_runs`` / ``agent_events`` so
a crashed run can be inspected and replayed. No step is re-executed more than
``agent.max_retries`` times, so there is no infinite agent loop.

An optional :class:`NeuroSanOrchestrator` implements the same
:class:`~app.agents.base.OrchestrationBackend` interface. It is disabled by
default and raises a descriptive error if the ``neuro_san`` package is not
installed — it can never silently fall back or fabricate behaviour.
"""
from __future__ import annotations

import time
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agents.base import (
    AgentInput,
    AgentOutput,
    OrchestrationBackend,
    WorkflowDefinition,
    WorkflowStep,
    digest,
    utc_now,
)
from app.agents.registry import AGENT_CLASSES
from app.agents.workflows import MAX_ROUNDS, WorkflowDefinition as _WF, get_workflow
from app.config import settings
from app.logging_config import get_logger
from app.models.audit import AgentEvent, AgentRun
from app.models.core import AgentEventStatus, AgentRunStatus, enum_value
from app.services.audit import record_audit, _dump  # noqa: F401

log = get_logger("regulens.orchestrator")


class NativeStateMachineOrchestrator(OrchestrationBackend):
    name = "native"

    def __init__(self, session: Session) -> None:
        self.session = session

    def available(self) -> bool:
        return True

    def status(self) -> dict[str, Any]:
        return {
            "backend": self.name,
            "available": True,
            "description": "Explicit Python state machine. Bounded steps, bounded retries, persisted state.",
            "deterministic_mode": settings.DETERMINISTIC_MODE,
        }

    # ------------------------------------------------------------------
    def run(self, workflow: WorkflowDefinition, payload: AgentInput) -> dict[str, Any]:
        run = AgentRun(
            run_uid=_uid("RUN", self.session, AgentRun, "run_uid"),
            workflow=workflow.name,
            correlation_id=payload.correlation_id,
            orchestration_backend=self.name,
            deterministic_mode=settings.DETERMINISTIC_MODE,
            status=AgentRunStatus.RUNNING,
            triggered_by=payload.actor,
            config=_dump({"params": payload.params}),
            started_at=utc_now(),
        )
        self.session.add(run)
        self.session.flush()

        step_outputs: dict[str, AgentOutput] = {}
        completed: list[str] = []
        failures: list[dict[str, Any]] = []
        sequence = 0
        overall = AgentRunStatus.SUCCEEDED

        for round_no in range(MAX_ROUNDS):
            run_any = False
            for step in workflow.steps:
                if step.name in completed:
                    continue
                if not all(dep in completed for dep in step.depends_on):
                    continue
                run_any = True
                agent_cls = AGENT_CLASSES.get(step.agent)
                if agent_cls is None:
                    failures.append({"step": step.name, "error": f"unknown agent '{step.agent}'"})
                    overall = AgentRunStatus.FAILED
                    break

                agent = agent_cls(self.session)
                step_payload = self._step_payload(payload, step, step_outputs)
                sequence += 1
                event = AgentEvent(
                    run_id=run.id,
                    agent_name=agent.name,
                    sequence=sequence,
                    status=AgentEventStatus.SKIPPED,
                    started_at=utc_now(),
                    input_digest=digest(step_payload.params),
                )
                self.session.add(event)
                self.session.flush()

                started = time.perf_counter()
                output, attempts, failure_kind = agent.run(step_payload)
                output.duration_ms = int((time.perf_counter() - started) * 1000)

                event.status = AgentEventStatus.SUCCEEDED if output.ok else AgentEventStatus.FAILED
                event.finished_at = utc_now()
                event.duration_ms = output.duration_ms
                event.attempt = attempts
                event.retries = output.retries
                event.message = output.summary
                event.error = output.error
                event.model_used = output.model_used
                event.rule_used = output.rule_used
                event.warnings = _dump(output.warnings)
                event.citations = _dump(output.citations[:20])
                event.output_digest = digest(output.data)
                event.output = output.model_dump_json()[:60000]
                self.session.flush()

                step_outputs[step.name] = output
                if output.ok:
                    completed.append(step.name)
                else:
                    failures.append(
                        {
                            "step": step.name,
                            "agent": agent.name,
                            "error": output.error,
                            "kind": failure_kind,
                        }
                    )
                    if step.stop_on_failure:
                        overall = AgentRunStatus.FAILED
                        break
                    overall = AgentRunStatus.PARTIAL

            if not run_any or overall in (AgentRunStatus.SUCCEEDED, AgentRunStatus.FAILED):
                break

        if failures and overall == AgentRunStatus.SUCCEEDED:
            overall = AgentRunStatus.PARTIAL

        run.finished_at = utc_now()
        run.duration_ms = int((run.finished_at - run.started_at).total_seconds() * 1000)
        run.status = overall
        run.summary = _dump(
            {
                "workflow": workflow.name,
                "steps_completed": completed,
                "steps_total": len(workflow.steps),
                "failures": failures,
                "outputs": {name: out.summary for name, out in step_outputs.items()},
            }
        )
        run.error = failures[0]["error"] if failures else None
        self.session.flush()

        record_audit(
            self.session,
            actor=payload.actor,
            action="agent_run.completed",
            entity_type="agent_run",
            entity_id=run.id,
            entity_uid=run.run_uid,
            correlation_id=payload.correlation_id,
            after={
                "workflow": workflow.name,
                "status": enum_value(overall),
                "steps_completed": completed,
                "failures": failures,
            },
        )
        log.info(
            "orchestrator.workflow_completed",
            workflow=workflow.name,
            run=run.run_uid,
            status=enum_value(overall),
            steps=len(completed),
        )
        return {
            "run_uid": run.run_uid,
            "workflow": workflow.name,
            "status": enum_value(overall),
            "steps_completed": completed,
            "failures": failures,
            "outputs": {name: out.model_dump(mode="json") for name, out in step_outputs.items()},
        }

    def _step_payload(
        self, payload: AgentInput, step: WorkflowStep, step_outputs: dict[str, AgentOutput]
    ) -> AgentInput:
        params = dict(payload.params)
        for dep in step.depends_on:
            out = step_outputs.get(dep)
            if out is None:
                continue
            params.setdefault(f"_from_{dep}", out.data)
            if dep == "compare_versions" and out.data.get("changes"):
                params.setdefault("change_id", out.data["changes"][0].get("id"))
        return AgentInput(correlation_id=payload.correlation_id, actor=payload.actor, params=params)


class NeuroSanOrchestrator(OrchestrationBackend):
    """Optional Cognizant Neuro SAN adapter.

    Neuro SAN is Apache-2.0 and installs cleanly, but its dependency closure
    requires ``openai`` / ``langchain-openai`` — commercial paid-API clients.
    Because constraint 2.1 forbids a mandatory paid LLM provider, this adapter is
    **opt-in and disabled by default**. It exports the same
    :class:`~app.agents.base.OrchestrationBackend` interface so the compliance
    engine is unaffected by the choice of orchestrator.

    Selection: ``ORCHESTRATION_BACKEND=neurosan`` **and** ``neuro_san`` must be
    importable. Otherwise a descriptive error is raised; the application never
    fabricates Neuro SAN behaviour.
    """

    name = "neurosan"

    def __init__(self, session: Session | None = None) -> None:
        self.session = session
        self._module = None
        try:
            import neuro_san  # type: ignore  # noqa: F401

            self._module = neuro_san
        except ImportError:
            self._module = None

    def available(self) -> bool:
        return self._module is not None

    def status(self) -> dict[str, Any]:
        if self._module is None:
            return {
                "backend": self.name,
                "available": False,
                "reason": "The 'neuro_san' package is not installed. See "
                          "docs/TECHNOLOGY_AND_LICENSES.md for why it is not a default dependency.",
                "interface": "app.agents.base.OrchestrationBackend",
            }
        return {
            "backend": self.name,
            "available": True,
            "module": getattr(self._module, "__file__", None),
            "interface": "app.agents.base.OrchestrationBackend",
        }

    def run(self, workflow: WorkflowDefinition, payload: AgentInput) -> dict[str, Any]:
        if not self.available():
            raise RuntimeError(
                "Neuro SAN orchestration was requested but the 'neuro_san' package is not installed. "
                "Install it (Apache-2.0) or set ORCHESTRATION_BACKEND=native. Note that Neuro SAN's "
                "reference integrations require a commercial LLM API key, which this project does not use."
            )
        # A real integration would translate ``workflow`` into a Neuro SAN agent
        # network definition here. That path is intentionally NOT faked.
        raise NotImplementedError(
            "Neuro SAN integration is intentionally not implemented in this build: it would require a "
            "commercial LLM API key, violating the project's no-paid-API constraint."
        )


def get_orchestrator(session: Session) -> OrchestrationBackend:
    if settings.ORCHESTRATION_BACKEND == "neurosan":
        return NeuroSanOrchestrator(session)
    return NativeStateMachineOrchestrator(session)


def run_workflow(session: Session, name: str, params: dict[str, Any], *, actor: str = "system") -> dict[str, Any]:
    workflow = get_workflow(name)
    payload = AgentInput(actor=actor, params=params)
    backend = get_orchestrator(session)
    return backend.run(workflow, payload)


def _uid(prefix: str, session: Session, model: Any, column: str) -> str:
    from datetime import datetime
    import secrets

    for _ in range(10):
        candidate = f"{prefix}-{datetime.utcnow().strftime('%y%m%d')}-{secrets.token_hex(3).upper()}"
        if not session.scalar(select(getattr(model, column)).where(getattr(model, column) == candidate).limit(1)):
            return candidate
    return f"{prefix}-{secrets.token_hex(8).upper()}"  # pragma: no cover


__all__ = [
    "NativeStateMachineOrchestrator",
    "NeuroSanOrchestrator",
    "get_orchestrator",
    "run_workflow",
]

