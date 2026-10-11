"""Agent boundary tests: retries, timeouts, NeuroSAN opt-in, audit."""
from __future__ import annotations

import pytest

from app.agents.base import AgentInput, AgentOutput, BaseAgent
from app.agents.orchestrator import NeuroSanOrchestrator, get_orchestrator


class FlakyAgent(BaseAgent):
    name = "flaky_agent"
    purpose = "test"
    permitted_tools = ()
    max_retries = 2

    def __init__(self):
        super().__init__()
        self.calls = 0

    def execute(self, payload: AgentInput) -> AgentOutput:
        self.calls += 1
        if self.calls < 3:
            raise RuntimeError("boom")
        return AgentOutput(summary="recovered")


class AlwaysFailAgent(BaseAgent):
    name = "fail_agent"
    purpose = "test"
    permitted_tools = ()
    max_retries = 1

    def execute(self, payload: AgentInput) -> AgentOutput:
        raise RuntimeError("permanent")


def test_retry_then_success():
    agent = FlakyAgent()
    out, attempts, kind = agent.run(AgentInput(params={}))
    assert out.ok and attempts == 3 and kind is None
    assert out.retries == 2


def test_retry_limit_respected():
    agent = AlwaysFailAgent()
    out, attempts, kind = agent.run(AgentInput(params={}))
    assert not out.ok and attempts == 2 and kind == "failed"
    assert "permanent" in (out.error or "")


def test_tool_permissions_enforced():
    from app.agents.base import ToolSet

    tools = ToolSet({"allowed_tool"}, {"allowed_tool": lambda: 1})
    assert tools.call("allowed_tool") == 1
    with pytest.raises(PermissionError):
        tools.call("shell_exec")


def test_neurosan_adapter_reports_unavailable(db):
    orch = NeuroSanOrchestrator(db)
    status = orch.status()
    assert status["backend"] == "neurosan"
    if not status["available"]:
        assert "neuro_san" in status["reason"]
        with pytest.raises(RuntimeError):
            orch.run(__import__("app.agents.base", fromlist=["WorkflowDefinition"]).WorkflowDefinition(
                name="x", description="x", steps=[]), AgentInput(params={}))


def test_orchestrator_defaults_to_native(db):
    assert get_orchestrator(db).name == "native"


def test_native_run_persists_events(db):
    from app.agents import run_workflow
    from app.models.audit import AgentEvent, AgentRun
    from sqlalchemy import select

    result = run_workflow(db, "evidence_review", {}, actor="tester")
    assert result["run_uid"]
    run = db.scalar(select(AgentRun).where(AgentRun.run_uid == result["run_uid"]))
    assert run is not None and str(run.status) in ("running", "succeeded", "partial", "failed")
    events = db.scalars(select(AgentEvent).where(AgentEvent.run_id == run.id)).all()
    assert len(events) >= 1


def test_llm_provider_never_fabricates():
    from app.llm.base import UnavailableProvider

    with pytest.raises(RuntimeError):
        UnavailableProvider().generate("hello")


def test_tfidf_provider_is_labelled_lexical():
    from app.llm.embeddings import TfidfEmbeddingProvider

    p = TfidfEmbeddingProvider()
    assert p.status()["kind"] == "lexical"
    vecs = p.embed(["the bank shall appoint an officer", "complaint redressal"])
    assert len(vecs) == 2 and len(vecs[0]) == p.dimension
