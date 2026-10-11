"""Remediation portfolio optimisation.

Models action selection as a **constrained multi-choice knapsack with
precedence and capacity constraints**:

* binary decision variable per remediation action
* budget constraint (Σ cost ≤ budget)
* capacity constraint (Σ duration ≤ capacity_days, when a capacity is given)
* precedence constraint (an action may start only after its dependencies)
* hard deadline enforcement (independent of the optimisation; see
  :func:`_deadline_violations`)
* horizon constraint (Σ duration along the critical path ≤ horizon_days)

Solvers:
* :class:`CpsatOptimiser` — OR-Tools CP-SAT (Apache-2.0) when importable.
* :class:`GreedyOptimiser` — deterministic fallback, always available.

Neither result claims global optimality unless the selected solver proves it;
the optimality claim is stored verbatim in the result.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Literal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.logging_config import get_logger
from app.models.core import RemediationStatus, ScenarioStrategy
from app.models.remediation import (
    ComplianceGap,
    RemediationAction,
    RemediationDependency,
    RemediationScenario,
    ScenarioResult,
)

log = get_logger("regulens.scenarios")

Strategy = Literal["max_risk_reduction", "deadline_first", "shared_control", "custom"]


@dataclass
class ActionCandidate:
    action_id: str
    action_uid: str
    gap_id: str
    gap_uid: str
    title: str
    risk_score: float
    expected_risk_reduction: float
    estimated_cost_inr: float
    cost_is_estimate: bool
    estimated_duration_days: int
    duration_is_estimate: bool
    deadline: date | None
    dependencies: list[str]
    required_capability: str | None
    obligation_ids: list[str] = field(default_factory=list)
    control_ids: list[str] = field(default_factory=list)
    shared_gaps: list[str] = field(default_factory=list)

    @property
    def value(self) -> float:
        """Objective contribution: expected absolute risk reduction."""
        return round(self.expected_risk_reduction * self.risk_score, 3)

    @property
    def value_per_day(self) -> float:
        if self.estimated_duration_days <= 0:
            return self.value
        return round(self.value / self.estimated_duration_days, 4)

    @property
    def value_per_rupee(self) -> float:
        if self.estimated_cost_inr <= 0:
            return self.value
        return round(self.value / self.estimated_cost_inr, 6)


@dataclass
class OptimiserOutput:
    selected: list[str]
    rejected: list[dict[str, Any]]
    constraint_checks: list[dict[str, Any]]
    objective_value: float
    solver: str
    optimality_claim: str
    explanation: list[dict[str, Any]]
    assumptions: list[str]
    feasible: bool = True
    infeasibility_reason: str | None = None


def load_candidates(
    session: Session,
    *,
    action_ids: list[str] | None = None,
    gap_ids: list[str] | None = None,
) -> list[ActionCandidate]:
    stmt = select(RemediationAction).where(
        RemediationAction.status.in_(
            [
                RemediationStatus.PROPOSED,
                RemediationStatus.APPROVED,
                RemediationStatus.IN_PROGRESS,
            ]
        )
    )
    if action_ids:
        stmt = stmt.where(RemediationAction.id.in_(action_ids))
    if gap_ids:
        stmt = stmt.where(RemediationAction.gap_id.in_(gap_ids))
    actions = list(session.scalars(stmt))

    gap_by_id = {g.id: g for g in session.scalars(select(ComplianceGap))} if actions else {}
    from app.models.remediation import RiskAssessment

    gap_risk: dict[str, float] = {}
    for row in session.execute(
        select(RiskAssessment.gap_id, RiskAssessment.score).order_by(RiskAssessment.created_at.desc())
    ).all():
        gap_risk.setdefault(row[0], row[1])

    deps: dict[str, list[str]] = {}
    for dep in session.scalars(select(RemediationDependency)).all():
        deps.setdefault(dep.action_id, []).append(dep.depends_on_action_id)

    by_action = {a.id: a for a in actions}
    candidates: list[ActionCandidate] = []
    for action in actions:
        gap = gap_by_id.get(action.gap_id)
        deps_ids = [d for d in deps.get(action.id, []) if d in by_action]
        obligation_ids: list[str] = []
        control_ids: list[str] = []
        if gap is not None:
            if gap.obligation_id:
                obligation_ids.append(gap.obligation_id)
            if gap.control_id:
                control_ids.append(gap.control_id)
        candidates.append(
            ActionCandidate(
                action_id=action.id,
                action_uid=action.action_uid,
                gap_id=action.gap_id,
                gap_uid=gap.gap_uid if gap else "unknown",
                title=action.proposed_action[:200],
                risk_score=gap_risk.get(action.gap_id, 50.0),
                expected_risk_reduction=float(action.expected_risk_reduction or 0.0),
                estimated_cost_inr=float(action.estimated_cost_inr or 0.0),
                cost_is_estimate=action.cost_is_estimate,
                estimated_duration_days=int(action.estimated_duration_days or 0),
                duration_is_estimate=action.duration_is_estimate,
                deadline=action.deadline,
                dependencies=deps_ids,
                required_capability=action.required_capability,
                obligation_ids=obligation_ids,
                control_ids=control_ids,
            )
        )
    # shared-control signal: actions touching the same control address several gaps
    control_map: dict[str, list[str]] = {}
    for cand in candidates:
        for cid in cand.control_ids:
            control_map.setdefault(cid, []).append(cand.action_id)
    for cand in candidates:
        for cid in cand.control_ids:
            cand.shared_gaps = sorted({a for a in control_map.get(cid, []) if a != cand.action_id})
    return candidates


# ---------------------------------------------------------------------------
# Optimisers
# ---------------------------------------------------------------------------
class CpsatOptimiser:
    name = "ortools_cpsat"
    claim = (
        "Optimality is proven for the solved formulation (CP-SAT returned status OPTIMAL). The formulation "
        "itself is a simplification of the real planning problem and the input estimates are uncertain."
    )
    feasible_claim = (
        "A feasible solution was found but optimality was not proven (solver time limit or status)."
    )

    def available(self) -> bool:
        try:
            import ortools  # noqa: F401

            return True
        except ImportError:  # pragma: no cover
            return False

    def solve(
        self,
        candidates: list[ActionCandidate],
        *,
        budget: float | None,
        capacity_days: float | None,
        horizon_days: int | None,
        strategy: str,
        team_capacity: dict[str, float] | None = None,
    ) -> OptimiserOutput:
        from ortools.sat.python import cp_model

        model = cp_model.CpModel()
        x: dict[str, Any] = {}
        for cand in candidates:
            x[cand.action_id] = model.NewBoolVar(f"x_{cand.action_uid}")

        # precedence
        for cand in candidates:
            for dep in cand.dependencies:
                if dep in x:
                    model.Add(x[cand.action_id] <= x[dep])

        if budget is not None:
            model.Add(
                sum(int(round(c.estimated_cost_inr)) * x[c.action_id] for c in candidates) <= int(round(budget))
            )
        if capacity_days is not None:
            model.Add(
                sum(int(c.estimated_duration_days) * x[c.action_id] for c in candidates) <= int(round(capacity_days))
            )
        if team_capacity:
            for capability, cap in team_capacity.items():
                model.Add(
                    sum(
                        int(c.estimated_duration_days) * x[c.action_id]
                        for c in candidates
                        if c.required_capability == capability
                    )
                    <= int(round(cap))
                )

        # objective by strategy (scaled by 1000 to keep CP-SAT integer precision)
        terms = []
        for cand in candidates:
            if strategy == "deadline_first":
                weight = max(1.0, 1.0 + max(0, (365 - (cand.deadline - date.today()).days if cand.deadline else 365)) / 365.0)
                terms.append(int(round(cand.value * weight * 1000)) * x[cand.action_id])
            elif strategy == "shared_control":
                weight = 1.0 + (0.25 * len(cand.shared_gaps))
                terms.append(int(round(cand.value * weight * 1000)) * x[cand.action_id])
            else:
                terms.append(int(round(cand.value * 1000)) * x[cand.action_id])
        model.Maximize(sum(terms))

        solver = cp_model.CpSolver()
        solver.parameters.max_time_in_seconds = 30.0
        solver.parameters.num_search_workers = 4
        status = solver.Solve(model)

        if status in (cp_model.OPTIMAL, cp_model.FEASIBLE):
            selected = [c.action_id for c in candidates if solver.Value(x[c.action_id]) == 1]
            claim = self.claim if status == cp_model.OPTIMAL else self.feasible_claim
            return OptimiserOutput(
                selected=selected,
                rejected=_reject(
                    candidates,
                    selected,
                    budget=budget,
                    capacity_days=capacity_days,
                    horizon_days=horizon_days,
                ),
                constraint_checks=_constraint_checks(candidates, selected, budget, capacity_days, horizon_days),
                objective_value=round(float(solver.ObjectiveValue()) / 1000.0, 3),
                solver=self.name,
                optimality_claim=claim,
                explanation=[_explain_row(c, c.action_id in selected, strategy) for c in candidates],
                assumptions=_assumptions(candidates, strategy, budget, capacity_days, horizon_days),
            )
        return OptimiserOutput(
            selected=[],
            rejected=[
                {"action_id": c.action_id, "action_uid": c.action_uid, "reason": "model infeasible"}
                for c in candidates
            ],
            constraint_checks=[],
            objective_value=0.0,
            solver=self.name,
            optimality_claim="No solution found: the constraint set is infeasible as specified.",
            explanation=[],
            assumptions=_assumptions(candidates, strategy, budget, capacity_days, horizon_days),
            feasible=False,
            infeasibility_reason="CP-SAT reported no feasible solution under the supplied constraints.",
        )


class GreedyOptimiser:
    name = "deterministic_greedy"
    claim = (
        "Deterministic greedy fallback. No optimality guarantee: actions are considered in a documented "
        "priority order and accepted when they still satisfy every hard constraint."
    )

    def available(self) -> bool:
        return True

    def solve(
        self,
        candidates: list[ActionCandidate],
        *,
        budget: float | None,
        capacity_days: float | None,
        horizon_days: int | None,
        strategy: str,
        team_capacity: dict[str, float] | None = None,
    ) -> OptimiserOutput:
        order = _priority_order(candidates, strategy)
        selected: list[str] = []
        used_cost = 0.0
        used_days = 0.0
        used_capability: dict[str, float] = {}
        by_id = {c.action_id: c for c in candidates}

        def reachable(action_id: str) -> bool:
            stack = list(by_id[action_id].dependencies)
            seen: set[str] = set()
            while stack:
                dep = stack.pop()
                if dep in seen:
                    continue
                seen.add(dep)
                if dep not in selected:
                    return False
                stack.extend(by_id[dep].dependencies if dep in by_id else [])
            return True

        for cand in order:
            new_cost = used_cost + cand.estimated_cost_inr
            new_days = used_days + cand.estimated_duration_days
            cap_key = cand.required_capability or "__none__"
            new_cap = used_capability.get(cap_key, 0.0) + cand.estimated_duration_days
            if budget is not None and new_cost > budget + 1e-9:
                continue
            if capacity_days is not None and new_days > capacity_days + 1e-9:
                continue
            if horizon_days is not None and _critical_path(by_id, selected + [cand.action_id]) > horizon_days:
                continue
            if team_capacity and cand.required_capability in team_capacity:
                if new_cap > team_capacity[cand.required_capability] + 1e-9:
                    continue
            if not reachable(cand.action_id):
                continue
            selected.append(cand.action_id)
            used_cost = new_cost
            used_days = new_days
            used_capability[cap_key] = new_cap

        return OptimiserOutput(
            selected=selected,
            rejected=_reject(candidates, selected, budget=budget, capacity_days=capacity_days, horizon_days=horizon_days),
            constraint_checks=_constraint_checks(candidates, selected, budget, capacity_days, horizon_days),
            objective_value=round(sum(by_id[c.action_id].value for c in candidates if c.action_id in selected), 3),
            solver=self.name,
            optimality_claim=self.claim,
            explanation=[_explain_row(c, c.action_id in selected, strategy) for c in candidates],
            assumptions=_assumptions(candidates, strategy, budget, capacity_days, horizon_days),
        )


def _priority_order(candidates: list[ActionCandidate], strategy: str) -> list[ActionCandidate]:
    if strategy == "deadline_first":
        return sorted(
            candidates,
            key=lambda c: (
                c.deadline or date(9999, 12, 31),
                -c.value,
                c.action_uid,
            ),
        )
    if strategy == "shared_control":
        return sorted(candidates, key=lambda c: (-len(c.shared_gaps), -c.value, c.action_uid))
    if strategy == "custom":
        return sorted(candidates, key=lambda c: (-c.value_per_day, -c.value, c.action_uid))
    return sorted(candidates, key=lambda c: (-c.value, c.action_uid))


def _critical_path(by_id: dict[str, ActionCandidate], selected: list[str]) -> int:
    total = 0
    for action_id in selected:
        cand = by_id[action_id]
        chain = [cand.estimated_duration_days]
        stack = list(cand.dependencies)
        while stack:
            dep = stack.pop()
            if dep in by_id and dep in selected:
                chain.append(by_id[dep].estimated_duration_days)
                stack.extend(by_id[dep].dependencies)
        total = max(total, sum(chain))
    return total


def _reject(
    candidates: list[ActionCandidate],
    selected: list[str],
    *,
    budget: float | None,
    capacity_days: float | None,
    horizon_days: int | None,
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    selected_cost = sum(c.estimated_cost_inr for c in candidates if c.action_id in selected)
    selected_days = sum(c.estimated_duration_days for c in candidates if c.action_id in selected)
    for cand in candidates:
        if cand.action_id in selected:
            continue
        reasons: list[str] = []
        if budget is not None and selected_cost + cand.estimated_cost_inr > budget + 1e-9:
            reasons.append("would exceed the remaining budget")
        if capacity_days is not None and selected_days + cand.estimated_duration_days > capacity_days + 1e-9:
            reasons.append("would exceed team capacity")
        if horizon_days is not None and cand.estimated_duration_days > horizon_days:
            reasons.append("this action alone exceeds the horizon")
        elif horizon_days is not None and not reasons:
            reasons.append("adding it would extend the critical path beyond the horizon")
        if not reasons:
            reasons.append("lower objective value than the selected portfolio under this strategy")
        out.append(
            {
                "action_id": cand.action_id,
                "action_uid": cand.action_uid,
                "gap_uid": cand.gap_uid,
                "reason": "; ".join(reasons),
                "forgone_value": cand.value,
            }
        )
    return out


def _constraint_checks(
    candidates: list[ActionCandidate],
    selected: list[str],
    budget: float | None,
    capacity_days: float | None,
    horizon_days: int | None,
) -> list[dict[str, Any]]:
    checks: list[dict[str, Any]] = []
    cost = sum(c.estimated_cost_inr for c in candidates if c.action_id in selected)
    days = sum(c.estimated_duration_days for c in candidates if c.action_id in selected)
    by_id = {c.action_id: c for c in candidates}
    checks.append(
        {
            "constraint": "budget",
            "limit": budget,
            "used": round(cost, 2),
            "satisfied": budget is None or cost <= budget + 1e-9,
            "note": "Costs marked as estimates are used as supplied; unknowns are treated as 0 and listed in assumptions.",
        }
    )
    checks.append(
        {
            "constraint": "team_capacity_days",
            "limit": capacity_days,
            "used": days,
            "satisfied": capacity_days is None or days <= capacity_days + 1e-9,
        }
    )
    checks.append(
        {
            "constraint": "horizon_days",
            "limit": horizon_days,
            "used": _critical_path(by_id, selected),
            "satisfied": horizon_days is None or _critical_path(by_id, selected) <= horizon_days,
        }
    )
    precedence_ok = all(
        all(dep in selected for dep in by_id[c.action_id].dependencies) for c in candidates if c.action_id in selected
    )
    checks.append({"constraint": "precedence", "limit": "dependencies must complete first", "used": None, "satisfied": precedence_ok})
    return checks


def _deadline_violations(candidates: list[ActionCandidate], selected: list[str]) -> list[dict[str, Any]]:
    today = date.today()
    out: list[dict[str, Any]] = []
    by_id = {c.action_id: c for c in candidates}
    for action_id in selected:
        cand = by_id[action_id]
        if cand.deadline and cand.deadline < today:
            out.append(
                {
                    "action_uid": cand.action_uid,
                    "deadline": cand.deadline.isoformat(),
                    "violation": "deadline already breached",
                    "severity": "hard",
                    "note": "Mandatory deadlines are enforced independently of the weighted objective and must "
                            "be escalated regardless of the optimisation result.",
                }
            )
    return out


def _explain_row(cand: ActionCandidate, selected: bool, strategy: str) -> dict[str, Any]:
    return {
        "action_uid": cand.action_uid,
        "gap_uid": cand.gap_uid,
        "selected": selected,
        "expected_risk_reduction": cand.expected_risk_reduction,
        "gap_risk_score": cand.risk_score,
        "objective_contribution": cand.value,
        "estimated_cost_inr": cand.estimated_cost_inr,
        "cost_is_estimate": cand.cost_is_estimate,
        "estimated_duration_days": cand.estimated_duration_days,
        "duration_is_estimate": cand.duration_is_estimate,
        "strategy": strategy,
        "why": (
            f"Selected under the '{strategy}' strategy: objective contribution {cand.value} "
            f"(reduction {cand.expected_risk_reduction:.2f} × gap risk {cand.risk_score:.1f}), "
            f"cost {cand.estimated_cost_inr}, duration {cand.estimated_duration_days}d"
            + (f", shared control covering {len(cand.shared_gaps)} other action(s)" if cand.shared_gaps else "")
            if selected
            else f"Not selected under the '{strategy}' strategy."
        ),
    }


def _assumptions(
    candidates: list[ActionCandidate],
    strategy: str,
    budget: float | None,
    capacity_days: float | None,
    horizon_days: int | None,
) -> list[str]:
    out = [
        f"Strategy: {strategy}. Objective = expected_risk_reduction × current gap risk score.",
        f"Budget: {'unbounded' if budget is None else f'{budget:,.0f} INR'} (actions with unknown cost are "
        f"treated as 0 and flagged).",
        f"Team capacity: {'unbounded' if capacity_days is None else f'{capacity_days} days'}.",
        f"Horizon: {'unbounded' if horizon_days is None else f'{horizon_days} days'}.",
        "Precedence constraints are honoured; a dependency is only complete if it is itself selected.",
    ]
    estimated_cost = sum(1 for c in candidates if c.cost_is_estimate)
    estimated_duration = sum(1 for c in candidates if c.duration_is_estimate)
    if estimated_cost:
        out.append(f"{estimated_cost} of {len(candidates)} action(s) have estimated (not known) costs.")
    if estimated_duration:
        out.append(f"{estimated_duration} of {len(candidates)} action(s) have estimated (not known) durations.")
    out.append(
        "Estimates are planning inputs, not measurements. Results are predictions until the remediation "
        "is completed and observed."
    )
    return out


def get_optimiser(prefer_cpsat: bool = True):
    if prefer_cpsat:
        cpsat = CpsatOptimiser()
        if cpsat.available():
            return cpsat
    return GreedyOptimiser()


def run_scenario(
    session: Session,
    scenario: RemediationScenario,
    *,
    action_ids: list[str] | None = None,
    gap_ids: list[str] | None = None,
    actor: str = "scenario_agent",
) -> ScenarioResult:
    from app.services.audit import record_audit

    candidates = load_candidates(session, action_ids=action_ids, gap_ids=gap_ids)
    strategy = str(scenario.strategy.value if hasattr(scenario.strategy, "value") else scenario.strategy)
    team_capacity: dict[str, float] | None = None
    assumptions_in = _load_json(scenario.assumptions) or {}
    if isinstance(assumptions_in, dict) and assumptions_in.get("team_capacity"):
        team_capacity = {str(k): float(v) for k, v in assumptions_in["team_capacity"].items()}

    optimiser = get_optimiser()
    output = optimiser.solve(
        candidates,
        budget=scenario.budget_inr,
        capacity_days=scenario.capacity_days,
        horizon_days=scenario.horizon_days,
        strategy=strategy,
        team_capacity=team_capacity,
    )

    by_id = {c.action_id: c for c in candidates}
    selected_cands = [by_id[i] for i in selected_ids(output.selected)]
    projected_cost = sum(c.estimated_cost_inr for c in selected_cands)
    projected_days = _critical_path(by_id, selected_ids(output.selected))
    unresolved = sorted({c.gap_id for c in candidates if c.action_id not in selected_ids(output.selected)})

    if not output.feasible:
        assumptions = output.assumptions
        assumptions.append(output.infeasibility_reason or "No feasible solution was produced by the solver.")
    else:
        assumptions = output.assumptions
    assumptions = list(dict.fromkeys(assumptions))

    deadline_violations = _deadline_violations(candidates, selected_ids(output.selected))
    if deadline_violations:
        assumptions.append(
            f"{len(deadline_violations)} selected action(s) breach a mandatory deadline. These must be escalated; "
            "the optimiser's objective does not override legal deadlines."
        )

    result = ScenarioResult(
        scenario_id=scenario.id,
        selected_actions=_json(
            [
                {
                    "action_id": c.action_id,
                    "action_uid": c.action_uid,
                    "gap_uid": c.gap_uid,
                    "objective_contribution": c.value,
                    "expected_risk_reduction": c.expected_risk_reduction,
                    "gap_risk_score": c.risk_score,
                    "estimated_cost_inr": c.estimated_cost_inr,
                    "cost_is_estimate": c.cost_is_estimate,
                    "estimated_duration_days": c.estimated_duration_days,
                    "duration_is_estimate": c.duration_is_estimate,
                    "deadline": c.deadline.isoformat() if c.deadline else None,
                }
                for c in selected_cands
            ]
        ),
        rejected_actions=_json(output.rejected),
        constraint_checks=_json(output.constraint_checks),
        objective_value=output.objective_value,
        projected_risk_reduction=round(
            sum(c.expected_risk_reduction * c.risk_score for c in selected_cands), 3
        ),
        projected_cost_inr=round(projected_cost, 2),
        projected_duration_days=projected_days,
        unresolved_gap_ids=_json(unresolved),
        solver=output.solver,
        optimality_claim=output.optimality_claim,
        explanation=_json(
            {
                "rows": output.explanation,
                "assumptions": assumptions,
                "deadline_violations": deadline_violations,
                "selected_count": len(selected_cands),
                "candidate_count": len(candidates),
            }
        ),
        assumptions=_json(assumptions),
        is_prediction=True,
    )
    session.add(result)
    session.flush()

    record_audit(
        session,
        actor=actor,
        action="scenario.executed",
        entity_type="remediation_scenario",
        entity_id=scenario.id,
        entity_uid=scenario.scenario_uid,
        after={
            "solver": result.solver,
            "objective": result.objective_value,
            "selected": len(selected_cands),
            "projected_cost": result.projected_cost_inr,
            "projected_days": result.projected_duration_days,
            "unresolved_gaps": len(unresolved),
        },
    )
    log.info(
        "scenario.executed",
        scenario=scenario.scenario_uid,
        solver=result.solver,
        selected=len(selected_cands),
        objective=result.objective_value,
    )
    return result


def selected_ids(selected: list[str]) -> list[str]:
    return list(selected)


def _load_json(value: Any) -> Any:
    if not value:
        return None
    if isinstance(value, str):
        try:
            return json.loads(value)
        except (TypeError, ValueError):
            return None
    return value


def _json(value: Any) -> str:
    return json.dumps(value, default=str)[:60000]


__all__ = [
    "load_candidates",
    "run_scenario",
    "CpsatOptimiser",
    "GreedyOptimiser",
    "get_optimiser",
    "ActionCandidate",
    "OptimiserOutput",
]
