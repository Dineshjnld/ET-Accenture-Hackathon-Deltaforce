"""Pydantic schemas for provider structured outputs.

Every hosted-model call that claims a structured result must validate against
one of these models. Malformed or unsupported outputs are rejected
(:class:`LLMInvalidOutputError`) — never coerced into fake findings.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class ObligationItem(BaseModel):
    obligation_text: str = Field(min_length=10, max_length=2000)
    quoted_text: str | None = Field(default=None, description="Exact verbatim source passage")
    responsible_party: str | None = None
    action_required: str | None = None
    applicability_conditions: list[str] = Field(default_factory=list)
    deadline_text: str | None = None
    severity: Literal["low", "medium", "high", "critical", "unknown"] = "unknown"
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)
    warnings: list[str] = Field(default_factory=list)


class ObligationList(BaseModel):
    obligations: list[ObligationItem] = Field(default_factory=list)


class MappingItem(BaseModel):
    control_uid: str = Field(min_length=1, max_length=120)
    rationale: str = Field(min_length=5, max_length=2000)
    confidence: float = Field(ge=0.0, le=1.0)
    warnings: list[str] = Field(default_factory=list)


class MappingList(BaseModel):
    mappings: list[MappingItem] = Field(default_factory=list)


class RemediationItem(BaseModel):
    proposed_action: str = Field(min_length=10, max_length=2000)
    reason: str = Field(min_length=5, max_length=2000)
    suggested_owner_role: str | None = None
    verification: str | None = None
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)
    warnings: list[str] = Field(default_factory=list)


class RemediationList(BaseModel):
    remediations: list[RemediationItem] = Field(default_factory=list)


class InterpretationResult(BaseModel):
    summary: str = Field(min_length=1, max_length=4000)
    key_points: list[str] = Field(default_factory=list)
    citations: list[str] = Field(default_factory=list)
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)
    requires_human_review: bool = True
    warnings: list[str] = Field(default_factory=list)


__all__ = [
    "ObligationItem",
    "ObligationList",
    "MappingItem",
    "MappingList",
    "RemediationItem",
    "RemediationList",
    "InterpretationResult",
]
