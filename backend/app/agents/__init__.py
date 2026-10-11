"""Agent layer: contracts, 13 logical agents, bounded orchestration."""
from __future__ import annotations

from app.agents.base import (
    AgentInput,
    AgentOutput,
    BaseAgent,
    OrchestrationBackend,
    ToolSet,
    WorkflowDefinition,
    WorkflowStep,
)
from app.agents.orchestrator import (
    NativeStateMachineOrchestrator,
    NeuroSanOrchestrator,
    get_orchestrator,
    run_workflow,
)
from app.agents.registry import AGENT_CLASSES, agent_catalog
from app.agents.workflows import MAX_ROUNDS, WORKFLOWS, get_workflow, list_workflows

__all__ = [
    "AgentInput",
    "AgentOutput",
    "BaseAgent",
    "ToolSet",
    "WorkflowDefinition",
    "WorkflowStep",
    "OrchestrationBackend",
    "NativeStateMachineOrchestrator",
    "NeuroSanOrchestrator",
    "get_orchestrator",
    "run_workflow",
    "AGENT_CLASSES",
    "agent_catalog",
    "WORKFLOWS",
    "get_workflow",
    "list_workflows",
    "MAX_ROUNDS",
]
