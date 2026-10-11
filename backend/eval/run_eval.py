"""ReguLens AI evaluation harness — measures, never fabricates.

Loads backend/eval/dataset.json, runs the deterministic engines, and prints a
JSON report with precision/recall/F1, citation correctness and per-case
outcomes. LLM behaviour is reported separately (skipped in deterministic mode).
"""
from __future__ import annotations

import json
import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.models.core import enum_value  # noqa: E402


def load_dataset() -> dict:
    return json.loads((Path(__file__).parent / "dataset.json").read_text(encoding="utf-8"))


def evaluate_extraction(ds: dict) -> dict:
    from app.services.obligations import extract_from_text

    tp = fp = fn = 0
    actor_ok = actor_total = 0
    deadline_ok = deadline_total = 0
    citation_ok = 0
    cases = []
    for case in ds["extraction_sentences"]:
        out = extract_from_text(case["sentence"])
        predicted = len(out) > 0
        expected = case["is_obligation"]
        if predicted and expected:
            tp += 1
            o = out[0]
            if o.quoted_text.strip() == case["sentence"].strip():
                citation_ok += 1
            if case.get("actor") is not None:
                actor_total += 1
                if (o.responsible_party or "") == case["actor"]:
                    actor_ok += 1
            if case.get("has_deadline"):
                deadline_total += 1
                if o.deadline_text:
                    deadline_ok += 1
            ok = True
        elif predicted and not expected:
            fp += 1
            ok = False
        elif not predicted and expected:
            fn += 1
            ok = False
        else:
            ok = True
        cases.append({"id": case["id"], "expected": expected, "predicted": predicted, "ok": ok})
    precision = tp / (tp + fp) if (tp + fp) else 1.0
    recall = tp / (tp + fn) if (tp + fn) else 1.0
    return {
        "true_positives": tp, "false_positives": fp, "false_negatives": fn,
        "precision": round(precision, 4), "recall": round(recall, 4),
        "f1": round(2 * precision * recall / (precision + recall), 4) if (precision + recall) else 0.0,
        "citation_correctness": round(citation_ok / tp, 4) if tp else 1.0,
        "actor_accuracy": round(actor_ok / actor_total, 4) if actor_total else None,
        "deadline_recall": round(deadline_ok / deadline_total, 4) if deadline_total else None,
        "cases": cases,
    }


def evaluate_mapping(ds: dict) -> dict:
    from app.models.controls import Control
    from app.models.obligations import Obligation
    from app.services.mapping import suggest_mappings

    class _S:
        pass

    tp = fp = fn = 0
    cases = []
    for case in ds["mapping_pairs"]:
        obl = Obligation(obligation_uid="E", regulation_id="r", regulation_version_id="v",
                         quoted_text=case["obligation"], obligation_text=case["obligation"])
        ctrl = Control(control_uid="C", title=case["control"], control_objective=case["control"],
                       control_activity=case["control"])
        cands = suggest_mappings(_S(), obl, controls=[ctrl], use_vectors=False, min_confidence=0.0)
        # decision rule under test: top-1 candidate accepted iff confidence >= default threshold
        from app.config import settings

        predicted = bool(cands) and cands[0].confidence >= settings.MAPPING_MIN_CONFIDENCE
        expected = case["valid"]
        if predicted and expected:
            tp += 1
        elif predicted and not expected:
            fp += 1
        elif not predicted and expected:
            fn += 1
        cases.append({"id": case["id"], "expected": expected, "predicted": predicted,
                      "confidence": cands[0].confidence if cands else 0.0,
                      "ok": predicted == expected})
    precision = tp / (tp + fp) if (tp + fp) else 1.0
    recall = tp / (tp + fn) if (tp + fn) else 1.0
    return {
        "true_positives": tp, "false_positives": fp, "false_negatives": fn,
        "precision": round(precision, 4), "recall": round(recall, 4),
        "f1": round(2 * precision * recall / (precision + recall), 4) if (precision + recall) else 0.0,
        "cases": cases,
    }


def evaluate_evidence(ds: dict) -> dict:
    from app.models.core import EvidenceValidity, ReviewStatus
    from app.models.evidence import Evidence
    from app.services.assessment import assess_evidence

    ok = 0
    cases = []
    for case in ds["evidence_cases"]:
        e = Evidence(
            evidence_uid=case["id"], title="eval",
            evidence_period_end=date.today() - timedelta(days=case["period_end_days_ago"]),
            review_status=ReviewStatus(case["review_status"]),
            test_result=case["test_result"],
        )
        got = enum_value(assess_evidence(e).validity)
        match = got == case["expected"]
        ok += 1 if match else 0
        cases.append({"id": case["id"], "expected": case["expected"], "got": got, "ok": match})
    return {"accuracy": round(ok / len(cases), 4), "correct": ok, "total": len(cases), "cases": cases}


def evaluate_changes(ds: dict) -> dict:
    from app.services.diffing import _classify_specific_change

    ok = 0
    cases = []
    for case in ds["change_cases"]:
        if case["expected_type"] is None:
            got = _classify_specific_change(case["left"], case["right"])
            match = got is None
            got_s = None if got is None else enum_value(got)
        else:
            got = _classify_specific_change(case["left"], case["right"])
            got_s = None if got is None else enum_value(got)
            match = got_s == case["expected_type"]
        ok += 1 if match else 0
        cases.append({"id": case["id"], "expected": case["expected_type"], "got": got_s, "ok": match})
    return {"accuracy": round(ok / len(cases), 4), "correct": ok, "total": len(cases), "cases": cases}


def evaluate_contradictions(ds: dict) -> dict:
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    import app.models  # noqa: F401
    from app.db import Base
    from app.models.controls import Policy
    from app.models.core import ReviewStatus
    from app.models.obligations import Obligation
    from app.services.contradiction import detect_obligation_vs_policy

    engine = create_engine("sqlite://", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    session = sessionmaker(bind=engine)()
    try:
        for case in ds["contradiction_cases"]:
            session.add(Obligation(obligation_uid=case["id"], regulation_id="r",
                                   regulation_version_id="v", quoted_text=case["obligation"],
                                   obligation_text=case["obligation"],
                                   review_status=ReviewStatus.APPROVED))
            session.add(Policy(code=case["id"], title=case["policy_title"],
                               description=case["policy_text"]))
        session.flush()
        findings = detect_obligation_vs_policy(session)
    finally:
        session.close()
        engine.dispose()
    ok = 0
    cases = []
    for case in ds["contradiction_cases"]:
        got = any(f.left_ref.get("uid") == case["id"] for f in findings)
        match = got == case["expect_finding"]
        ok += 1 if match else 0
        cases.append({"id": case["id"], "expected": case["expect_finding"], "got": got, "ok": match})
    return {"accuracy": round(ok / len(cases), 4), "correct": ok, "total": len(cases),
            "findings": len(findings), "cases": cases}


def main() -> None:
    ds = load_dataset()
    report = {
        "dataset": ds["version"],
        "mode": "deterministic (no LLM)",
        "extraction": evaluate_extraction(ds),
        "mapping": evaluate_mapping(ds),
        "evidence": evaluate_evidence(ds),
        "change_classification": evaluate_changes(ds),
        "contradiction": evaluate_contradictions(ds),
        "llm": {"status": "not evaluated separately — no local model is installed in this environment"},
        "limitations": [
            "Small hand-labelled set; measures the bundled rules, not regulatory truth.",
            "Mapping decision rule uses the configured default confidence threshold.",
            "Test/eval data are disjoint from the demo seed by construction (eval uses its own sentences).",
        ],
    }
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
