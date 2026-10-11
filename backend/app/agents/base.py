"""Agent contracts and the orchestration backend interface.

Every logical agent has:
* a typed input schema (:class:`AgentInput`)
* a typed output schema (:class:`AgentOutput`)
* an explicit, finite tool set (``permitted_tools``)
* timeout, bounded retries and an explicit failure state
* structured events with a correlation ID

**Prompt-injection defence.** Document text is untrusted data. Agents never
pass raw document text to a place where it could alter control flow; the LLM
providers wrap it in ``<untrusted_document>`` tags and the orchestrator records
injection warnings instead of acting on them.
"""
from __future__ import annotations

import hashlib
import json
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Iterable

from pydantic import BaseModel, Field

from app.logging_config import get_logger

log = get_logger("regulens.agents")


# ---------------------------------------------------------------------------
# Typed IO
# ---------------------------------------------------------------------------
class AgentInput(BaseModel):
    """Base typed input for every agent."""

    correlation_id: str = Field(default_factory=lambda: f"corr-{time.time_ns()}")
    actor: str = "system"
    params: dict[str, Any] = Field(default_factory=dict)


class AgentOutput(BaseModel):
    """Base typed output for every agent."""

    ok: bool = True
    summary: str = ""
    data: dict[str, Any] = Field(default_factory=dict)
    citations: list[dict[str, Any]] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    model_used: str | None = None
    rule_used: str | None = None
    deterministic: bool = True
    duration_ms: int = 0
    retries: int = 0
    error: str | None = None


# ---------------------------------------------------------------------------
# Tool permissions
# ---------------------------------------------------------------------------
class ToolSet:
    """Explicit, finite tool registry. Agents may only use listed tools."""

    def __init__(self, names: Iterable[str], handlers: dict[str, Any] | None = None) -> None:
        self.names = frozenset(names)
        self._handlers = handlers or {}

    def has(self, name: str) -> bool:
        return name in self.names

    def call(self, name: str, **kwargs: Any) -> Any:
        if not self.has(name):
            raise PermissionError(f"Tool '{name}' is not permitted for this agent")
        handler = self._handlers.get(name)
        if handler is None:
            raise PermissionError(f"Tool '{name}' has no implementation")
        return handler(**kwargs)

    def __contains__(self, name: str) -> bool:  # pragma: no cover
        return self.has(name)

    def describe(self) -> list[str]:
        return sorted(self.names)


# ---------------------------------------------------------------------------
# Base agent
# ---------------------------------------------------------------------------
class BaseAgent(ABC):
    name: str = "agent"
    purpose: str = ""
    permitted_tools: tuple[str, ...] = ()
    timeout_seconds: int = 300
    max_retries: int = 2

    def __init__(self, tools: ToolSet | None = None) -> None:
        self.tools = tools or ToolSet(self.permitted_tools)

    # -- contract ---------------------------------------------------------
    @abstractmethod
    def execute(self, payload: AgentInput) -> AgentOutput:
        """Run the agent. Must return a typed output including failure state."""

    # -- helpers ----------------------------------------------------------
    def describe(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "purpose": self.purpose,
            "permitted_tools": list(self.permitted_tools),
            "timeout_seconds": self.timeout_seconds,
            "max_retries": self.max_retries,
        }

    def run(self, payload: AgentInput) -> tuple[AgentOutput, int, str | None]:
        """Execute with bounded retries and a wall-clock timeout check."""
        started = time.perf_counter()
        attempt = 0
        last_error: Exception | None = None
        while attempt <= self.max_retries:
            attempt += 1
            try:
                output = self.execute(payload)
                output.duration_ms = int((time.perf_counter() - started) * 1000)
                output.retries = attempt - 1
                return output, attempt, None
            except Exception as exc:  # noqa: BLE001 - agent boundary
                last_error = exc
                log.warning(
                    "agent.attempt_failed",
                    agent=self.name,
                    attempt=attempt,
                    error=str(exc),
                    correlation_id=payload.correlation_id,
                )
        elapsed = time.perf_counter() - started
        if elapsed > self.timeout_seconds:
            return (
                AgentOutput(
                    ok=False,
                    summary=f"Agent {self.name} exceeded its {self.timeout_seconds}s budget",
                    error="timeout",
                    duration_ms=int(elapsed * 1000),
                    retries=attempt - 1,
                ),
                attempt,
                "timeout",
            )
        return (
            AgentOutput(
                ok=False,
                summary=f"Agent {self.name} failed after {attempt} attempt(s)",
                error=f"{type(last_error).__name__}: {last_error}",
                duration_ms=int(elapsed * 1000),
                retries=attempt - 1,
            ),
            attempt,
            "failed",
        )


# ---------------------------------------------------------------------------
# Orchestration backend interface
# ---------------------------------------------------------------------------
@dataclass
class WorkflowStep:
    name: str
    agent: str
    depends_on: list[str] = field(default_factory=list)
    stop_on_failure: bool = False


@dataclass
class WorkflowDefinition:
    name: str
    description: str
    steps: list[WorkflowStep]


class OrchestrationBackend(ABC):
    """Interface every orchestration implementation must satisfy.

    * :class:`app.agents.orchestrator.NativeStateMachineOrchestrator` — the
      default. Pure Python, explicit state machine, persisted in
      ``agent_runs`` / ``agent_events``.
    * :class:`app.agents.orchestrator.NeuroSanOrchestrator` — optional adapter
      for Cognizant Neuro SAN. Disabled by default (see docs).
    """

    name: str = "abstract"

    @abstractmethod
    def run(self, workflow: WorkflowDefinition, payload: AgentInput) -> dict[str, Any]:
        """Execute a workflow and return a persisted run record."""

    @abstractmethod
    def available(self) -> bool: ...

    @abstractmethod
    def status(self) -> dict[str, Any]: ...


def digest(value: Any) -> str:
    try:
        raw = json.dumps(value, sort_keys=True, default=str)
    except (TypeError, ValueError):  # pragma: no cover
        raw = str(value)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def cap_output(payload: AgentOutput, limit: int = 60000) -> str:
    """Serialise an agent output, capped in size, never containing secrets."""
    return _safe(payload)[:limit]


def _safe(payload: AgentOutput) -> str:
    return json.dumps(payload.model_dump(mode="json"), default=str)


__all__ = [
    "AgentInput",
    "AgentOutput",
    "BaseAgent",
    "ToolSet",
    "WorkflowDefinition",
    "WorkflowStep",
    "OrchestrationBackend",
    "digest",
    "utc_now",
]
