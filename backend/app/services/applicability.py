"""Applicability determination.

Applicability is never guessed. The engine evaluates explicit rules against an
explicit organisation/business-unit profile. Where a rule cannot be evaluated
because an attribute is unknown, the result is ``unknown`` with the missing
attribute named — never ``applicable``.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Mapping

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.logging_config import get_logger
from app.models.core import ReviewStatus
from app.models.identity import BusinessUnit, Organization
from app.models.obligations import ApplicabilityAssessment, ApplicabilityRule, Obligation

log = get_logger("regulens.applicability")

OPERATORS: dict[str, Any] = {
    "eq": lambda a, b: a == b,
    "ne": lambda a, b: a != b,
    "in": lambda a, b: a in (b or []),
    "not_in": lambda a, b: a not in (b or []),
    "gte": lambda a, b: a is not None and b is not None and float(a) >= float(b),
    "lte": lambda a, b: a is not None and b is not None and float(a) <= float(b),
    "gt": lambda a, b: a is not None and b is not None and float(a) > float(b),
    "lt": lambda a, b: a is not None and b is not None and float(a) < float(b),
    "contains": lambda a, b: b is not None and str(b).lower() in str(a).lower(),
}

# Attributes the rule engine understands. Anything outside this list cannot be
# asserted and therefore cannot be used to conclude applicability.
KNOWN_ATTRIBUTES = {
    "organization_id",
    "entity_type",
    "jurisdiction",
    "total_assets_inr_crore",
    "banking_outlets_in_india",
    "has_internal_ombudsman",
    "business_unit_code",
    "business_unit_name",
}


@dataclass
class ApplicabilityOutcome:
    applicable: str  # yes | no | unknown
    rationale: str
    missing_attributes: list[str]
    rule_id: str | None
    rule_name: str | None
    conditions_evaluated: list[dict[str, Any]]


def build_profile(org: Organization, bu: BusinessUnit | None = None) -> dict[str, Any]:
    profile: dict[str, Any] = {
        "organization_id": org.id,
        "entity_type": org.entity_type,
        "jurisdiction": org.jurisdiction,
        "total_assets_inr_crore": org.total_assets_inr_crore,
        "banking_outlets_in_india": org.banking_outlets_in_india,
        "has_internal_ombudsman": org.has_internal_ombudsman,
    }
    if bu is not None:
        profile["business_unit_code"] = bu.code
        profile["business_unit_name"] = bu.name
    return profile


def evaluate_rule(rule: ApplicabilityRule, profile: Mapping[str, Any]) -> ApplicabilityOutcome:
    try:
        conditions = json.loads(rule.conditions)
    except (TypeError, ValueError):
        return ApplicabilityOutcome(
            "unknown",
            f"Rule '{rule.name}' has malformed conditions and was not evaluated.",
            [],
            rule.id,
            rule.name,
            [],
        )
    if not isinstance(conditions, list) or not conditions:
        return ApplicabilityOutcome(
            "unknown", f"Rule '{rule.name}' defines no evaluable conditions.", [], rule.id, rule.name, []
        )

    missing: list[str] = []
    evaluated: list[dict[str, Any]] = []
    all_matched = True
    for cond in conditions:
        field = cond.get("field")
        op = cond.get("operator", "eq")
        expected = cond.get("value")
        actual = profile.get(field, "__MISSING__")
        if actual == "__MISSING__" or actual is None:
            missing.append(field)
            evaluated.append({"field": field, "operator": op, "expected": expected, "actual": None, "result": "unknown"})
            all_matched = False
            continue
        fn = OPERATORS.get(op)
        if fn is None:
            return ApplicabilityOutcome(
                "unknown",
                f"Rule '{rule.name}' uses unsupported operator '{op}'.",
                [],
                rule.id,
                rule.name,
                evaluated,
            )
        try:
            matched = bool(fn(actual, expected))
        except (TypeError, ValueError):
            matched = False
        evaluated.append(
            {"field": field, "operator": op, "expected": expected, "actual": actual, "result": matched}
        )
        all_matched = all_matched and matched

    if missing:
        return ApplicabilityOutcome(
            "unknown",
            f"Cannot determine applicability: required attribute(s) {', '.join(sorted(set(missing)))} are unknown "
            f"for this organisation/business unit.",
            sorted(set(missing)),
            rule.id,
            rule.name,
            evaluated,
        )
    if all_matched:
        # Rule fires: its declared outcome decides.
        outcome = "yes" if rule.outcome == "applicable" else "no"
        return ApplicabilityOutcome(
            outcome,
            f"Rule '{rule.name}' fired: conditions matched; declared outcome={rule.outcome}.",
            [],
            rule.id,
            rule.name,
            evaluated,
        )
    # Rule does not fire. Gate-type rules (thresholds, jurisdiction) negate on
    # non-match — failing the gate means not applicable. Scoping rules merely
    # abstain so unrelated scopes never shadow each other.
    if (rule.rule_type or "").endswith("_gate") or rule.rule_type == "exclusion":
        outcome = "no" if rule.outcome == "applicable" else "yes"
        return ApplicabilityOutcome(
            outcome,
            f"Rule '{rule.name}' is a gate and its conditions did not hold — "
            f"negated outcome={outcome}.",
            [],
            rule.id,
            rule.name,
            evaluated,
        )
    return ApplicabilityOutcome(
        "abstain",
        f"Rule '{rule.name}' does not fire for this profile and abstains.",
        [],
        rule.id,
        rule.name,
        evaluated,
    )


def assess_obligation(
    session: Session,
    obligation: Obligation,
    *,
    organization: Organization,
    business_unit: BusinessUnit | None = None,
    persist: bool = True,
) -> ApplicabilityOutcome:
    """Evaluate all active rules for an obligation.

    Every active rule is evaluated (priority order is reporting order only).
    Combination semantics, chosen so exclusions can never be shadowed by
    general inclusion rules:

    * any determinate ``no``  → ``no`` (the excluding rule is cited);
    * else any ``unknown``    → ``unknown`` with the missing attributes named;
    * else any ``yes``        → ``yes``;
    * else                    → ``unknown``.
    """
    profile = build_profile(organization, business_unit)
    rules = list(
        session.scalars(
            select(ApplicabilityRule)
            .where(ApplicabilityRule.is_active.is_(True))
            .order_by(ApplicabilityRule.priority, ApplicabilityRule.created_at)
        )
    )
    if not rules:
        outcome = ApplicabilityOutcome(
            "unknown",
            "No applicability rules are configured — applicability cannot be asserted.",
            ["applicability_rules"],
            None,
            None,
            [],
        )
    else:
        results = [evaluate_rule(rule, profile) for rule in rules]
        votes = [r for r in results if r.applicable in ("yes", "no", "unknown")]
        noes = [r for r in votes if r.applicable == "no"]
        unknowns = [r for r in votes if r.applicable == "unknown"]
        yeses = [r for r in votes if r.applicable == "yes"]
        if noes:
            first = noes[0]
            outcome = ApplicabilityOutcome(
                "no",
                f"Not applicable: {first.rationale} "
                f"({len(yeses)} inclusion vote(s) overridden by an exclusion).",
                [],
                first.rule_id,
                first.rule_name,
                first.conditions_evaluated,
            )
        elif unknowns:
            outcome = ApplicabilityOutcome(
                "unknown",
                "At least one gate could not be evaluated: "
                + " | ".join(o.rationale for o in unknowns),
                sorted({m for o in unknowns for m in o.missing_attributes}),
                None,
                None,
                [c for o in unknowns for c in o.conditions_evaluated][:20],
            )
        elif yeses:
            first = yeses[0]
            outcome = ApplicabilityOutcome(
                "yes",
                f"{first.rationale} ({len(yeses)} of {len(votes)} voting rules concur; "
                f"{len(results) - len(votes)} abstained).",
                [],
                first.rule_id,
                first.rule_name,
                [c for o in votes for c in o.conditions_evaluated][:20],
            )
        else:
            outcome = ApplicabilityOutcome(
                "unknown",
                "No active rule fired for this profile — applicability cannot be asserted.",
                [],
                None,
                None,
                [],
            )

    if persist:
        session.add(
            ApplicabilityAssessment(
                obligation_id=obligation.id,
                rule_id=outcome.rule_id,
                organization_id=organization.id,
                business_unit_id=business_unit.id if business_unit else None,
                applicable=outcome.applicable,
                rationale=outcome.rationale,
                evaluated_inputs=json.dumps(
                    {"profile": profile, "conditions": outcome.conditions_evaluated}, default=str
                ),
                assessed_by="rule_engine",
            )
        )
        session.flush()
    return outcome


def seed_default_rules(session: Session) -> list[ApplicabilityRule]:
    """Create the built-in, transparent applicability rules."""
    from app.services.audit import _dump  # noqa: F401

    specs = [
        (
            "RBI directions scoped to commercial banks",
            "Master Directions addressed to scheduled commercial banks apply to banks of that entity type.",
            [
                {"field": "entity_type", "operator": "in", "value": ["scheduled_commercial_bank", "public_sector_bank"]}
            ],
            "applicable",
            10,
        ),
        (
            "RBI directions scoped to small finance banks",
            "Applies where the entity type is a Small Finance Bank.",
            [{"field": "entity_type", "operator": "in", "value": ["small_finance_bank"]}],
            "applicable",
            20,
        ),
        (
            "RBI directions scoped to NBFCs",
            "Applies where the entity type is an NBFC.",
            [{"field": "entity_type", "operator": "in", "value": ["nbfc"]}],
            "applicable",
            30,
        ),
        (
            "Jurisdiction gate",
            "Indian RBI instruments do not apply to entities outside India.",
            [{"field": "jurisdiction", "operator": "eq", "value": "IN"}],
            "applicable",
            5,
            "jurisdiction_gate",
        ),
        (
            "Internal Ombudsman threshold — 10 or more outlets",
            "RBI (Commercial Banks - Internal Ombudsman) Directions, 2026 apply to banks having 10 or more "
            "banking outlets in India. Below the threshold the obligation is not applicable; where the outlet "
            "count is unknown the result is unknown.",
            [{"field": "banking_outlets_in_india", "operator": "gte", "value": 10}],
            "applicable",
            40,
            "threshold_gate",
        ),
        (
            "Regulated entity already has an Internal Ombudsman",
            "Obligations concerning the appointment/operation of an IO presuppose that the entity has one.",
            [{"field": "has_internal_ombudsman", "operator": "eq", "value": True}],
            "applicable",
            50,
        ),
    ]
    created: list[ApplicabilityRule] = []
    for spec in specs:
        name, description, conditions, outcome, priority = spec[:5]
        rule_type = spec[5] if len(spec) > 5 else "entity_scope"
        exists = session.scalar(select(ApplicabilityRule).where(ApplicabilityRule.name == name).limit(1))
        if exists:
            created.append(exists)
            continue
        rule = ApplicabilityRule(
            name=name,
            description=description,
            rule_type=rule_type,
            conditions=json.dumps(conditions),
            outcome=outcome,
            priority=priority,
            created_by="system",
        )
        session.add(rule)
        created.append(rule)
    session.flush()
    return created


__all__ = [
    "ApplicabilityOutcome",
    "assess_obligation",
    "evaluate_rule",
    "build_profile",
    "seed_default_rules",
    "KNOWN_ATTRIBUTES",
]
