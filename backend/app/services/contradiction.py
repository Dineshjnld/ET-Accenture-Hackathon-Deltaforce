"""Regulatory contradiction detection.

Detects *potential* conflicts between obligations, internal policies, control
descriptions and document versions. Every finding records both conflicting
passages and an explanation of why it may only be an *apparent* conflict (scope,
date, jurisdiction, or exception). Nothing is auto-resolved.
"""
from __future__ import annotations

import difflib
import json
import re
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.logging_config import get_logger
from app.models.core import ReviewStatus
from app.models.controls import Control, Policy
from app.models.obligations import Obligation

log = get_logger("regulens.contradiction")

_NEGATION_RE = re.compile(r"\b(shall\s+not|must\s+not|may\s+not|is\s+not\s+permitted|prohibited)\b", re.I)
_OBLIGATION_RE = re.compile(r"\b(shall|must)\b", re.I)
_NUMBER_RE = re.compile(r"\b(\d{1,7}(?:,\d{3})*(?:\.\d+)?)\b")
_DATE_RE = re.compile(r"\b(\d{1,2}\s+[A-Z][a-z]+\.?\s+\d{4}|[A-Z][a-z]+\s+\d{1,2},\s+\d{4})\b")


@dataclass
class Contradiction:
    kind: str
    confidence: float
    left_ref: dict[str, Any]
    right_ref: dict[str, Any]
    left_text: str
    right_text: str
    explanation: str
    possible_explanation: str
    resolution_status: str = "requires_review"
    warnings: list[str] = field(default_factory=list)


@dataclass
class ContradictionReport:
    findings: list[Contradiction]
    scanned: int


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip())


def _clip(text: str, limit: int = 400) -> str:
    text = _clean(text)
    return text if len(text) <= limit else text[: limit - 3] + "..."


def _similarity(a: str, b: str) -> float:
    return difflib.SequenceMatcher(None, (a or "").lower(), (b or "").lower()).ratio()


def _same_subject(a: str, b: str, threshold: float = 0.55) -> bool:
    return _similarity(a, b) >= threshold


def detect_obligation_vs_policy(session: Session) -> list[Contradiction]:
    findings: list[Contradiction] = []
    obligations = list(
        session.scalars(select(Obligation).where(Obligation.review_status == ReviewStatus.APPROVED))
    )
    policies = list(session.scalars(select(Policy)))
    if not obligations or not policies:
        return findings

    for obl in obligations:
        for policy in policies:
            if not policy.title:
                continue
            policy_text = _clean(policy.description or policy.title)
            if not policy_text:
                continue
            # Subject gate: the obligation must concern the same subject as the
            # policy title OR the policy body. Titles are short, so the title
            # bar is lower; the body bar is higher to avoid spurious matches.
            same_subject = _same_subject(obl.obligation_text, policy.title, 0.5) or _same_subject(
                obl.obligation_text, policy_text, 0.6
            )
            if not same_subject:
                continue
            obl_requires = bool(_OBLIGATION_RE.search(obl.quoted_text))
            policy_forbids = bool(_NEGATION_RE.search(policy_text))
            if obl_requires and policy_forbids:
                findings.append(
                    Contradiction(
                        kind="obligation_vs_policy",
                        confidence=0.55,
                        left_ref={"type": "obligation", "id": obl.id, "uid": obl.obligation_uid},
                        right_ref={"type": "policy", "id": policy.id, "uid": policy.code},
                        left_text=_clip(obl.quoted_text),
                        right_text=_clip(policy_text),
                        explanation=(
                            "An approved obligation requires an action while the internal policy text on the "
                            "same subject uses prohibitive language."
                        ),
                        possible_explanation=(
                            "The policy may contain a narrower exception, apply to a different entity scope "
                            "or a different period, or the policy may predate the current regulation version. "
                            "Read both passages before concluding a conflict exists."
                        ),
                    )
                )
            obl_numbers = set(_NUMBER_RE.findall(obl.quoted_text))
            policy_numbers = set(_NUMBER_RE.findall(policy_text))
            if obl_numbers and policy_numbers and not (obl_numbers & policy_numbers):
                if same_subject:
                    findings.append(
                        Contradiction(
                            kind="threshold_mismatch",
                            confidence=0.45,
                            left_ref={"type": "obligation", "id": obl.id, "uid": obl.obligation_uid},
                            right_ref={"type": "policy", "id": policy.id, "uid": policy.code},
                            left_text=_clip(obl.quoted_text),
                            right_text=_clip(policy_text),
                            explanation=(
                                f"The obligation cites thresholds {sorted(obl_numbers)} while the policy cites "
                                f"{sorted(policy_numbers)} on a similar subject."
                            ),
                            possible_explanation=(
                                "Different units, periods or scopes can make both figures correct. Verify the "
                                "regulatory version in force before escalating."
                            ),
                        )
                    )
    return findings


def detect_control_vs_control(session: Session) -> list[Contradiction]:
    findings: list[Contradiction] = []
    controls = list(session.scalars(select(Control)))
    for i, left in enumerate(controls):
        for right in controls[i + 1 :]:
            if not left.control_activity or not right.control_activity:
                continue
            if left.control_type == "unknown" or right.control_type == "unknown":
                continue
            if left.control_type == right.control_type:
                continue
            if not _same_subject(left.control_objective or left.title, right.control_objective or right.title, 0.7):
                continue
            findings.append(
                Contradiction(
                    kind="control_type_mismatch",
                    confidence=0.4,
                    left_ref={
                        "type": "control",
                        "id": left.id,
                        "uid": left.control_uid,
                        "control_type": left.control_type,
                    },
                    right_ref={
                        "type": "control",
                        "id": right.id,
                        "uid": right.control_uid,
                        "control_type": right.control_type,
                    },
                    left_text=_clip(left.control_activity),
                    right_text=_clip(right.control_activity),
                    explanation=(
                        f"Two controls address the same objective but are classified differently "
                        f"({left.control_type} vs {right.control_type})."
                    ),
                    possible_explanation=(
                        "The two controls may legitimately differ in scope (one preventive, one detective "
                        "backstop). Confirm whether both are intended."
                    ),
                )
            )
    return findings


def detect_obligation_vs_obligation(session: Session) -> list[Contradiction]:
    findings: list[Contradiction] = []
    obligations = list(
        session.scalars(
            select(Obligation).where(
                Obligation.review_status == ReviewStatus.APPROVED,
                Obligation.effective_date.is_not(None),
            )
        )
    )
    for i, left in enumerate(obligations):
        for right in obligations[i + 1 :]:
            if left.regulation_id == right.regulation_id:
                continue
            if not _same_subject(left.obligation_text, right.obligation_text, 0.75):
                continue
            if not left.deadline_date or not right.deadline_date:
                continue
            if left.deadline_date == right.deadline_date:
                continue
            findings.append(
                Contradiction(
                    kind="conflicting_deadlines",
                    confidence=0.5,
                    left_ref={"type": "obligation", "id": left.id, "uid": left.obligation_uid},
                    right_ref={"type": "obligation", "id": right.id, "uid": right.obligation_uid},
                    left_text=_clip(left.quoted_text),
                    right_text=_clip(right.quoted_text),
                    explanation=(
                        f"Similar obligations from different regulations impose different deadlines "
                        f"({left.deadline_date.isoformat()} vs {right.deadline_date.isoformat()})."
                    ),
                    possible_explanation=(
                        "The obligations may apply to different entities, jurisdictions or reporting cycles. "
                        "Do not assume one supersedes the other without review."
                    ),
                )
            )
    return findings


def detect_all(session: Session) -> ContradictionReport:
    findings: list[Contradiction] = []
    findings.extend(detect_obligation_vs_policy(session))
    findings.extend(detect_control_vs_control(session))
    findings.extend(detect_obligation_vs_obligation(session))
    scanned = len(list(session.scalars(select(Obligation))))
    findings.sort(key=lambda f: f.confidence, reverse=True)
    log.info("contradictions.detected", count=len(findings))
    return ContradictionReport(findings=findings, scanned=scanned)


def report_to_json(report: ContradictionReport) -> dict[str, Any]:
    return {
        "findings": [
            {
                "kind": f.kind,
                "confidence": f.confidence,
                "left_ref": f.left_ref,
                "right_ref": f.right_ref,
                "left_text": f.left_text,
                "right_text": f.right_text,
                "explanation": f.explanation,
                "possible_explanation": f.possible_explanation,
                "resolution_status": f.resolution_status,
                "warnings": f.warnings,
            }
            for f in report.findings
        ],
        "count": len(report.findings),
        "scanned_obligations": report.scanned,
        "disclaimer": (
            "These are candidate conflicts detected by heuristic rules. Each finding states why it may be an "
            "apparent conflict. None is resolved automatically; a qualified reviewer must adjudicate."
        ),
    }


__all__ = ["detect_all", "report_to_json", "Contradiction", "ContradictionReport"]
