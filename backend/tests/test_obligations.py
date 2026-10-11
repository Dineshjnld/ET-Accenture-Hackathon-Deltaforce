"""Obligation extraction tests: determinism, citations, schema honesty."""
from __future__ import annotations

from app.services.obligations import extract_from_text


def test_extracts_shall_sentence_with_citation():
    text = "(1) Every bank shall appoint at least one IO.\n(2) Preamble without force."
    out = extract_from_text(text, source_page=3, source_section="Chapter II")
    assert len(out) == 1
    o = out[0]
    assert o.quoted_text == "(1) Every bank shall appoint at least one IO."
    assert o.source_page == 3 and o.source_section == "Chapter II"
    assert o.responsible_party == "Bank"
    assert o.action_required == "Appoint"
    assert 0.3 <= o.extraction_confidence <= 0.9


def test_definitions_are_not_obligations():
    text = '"Deficiency in service" means a shortcoming in any service the bank provides.'
    assert extract_from_text(text) == []


def test_short_headings_are_not_obligations():
    assert extract_from_text("Chapter II") == []
    assert extract_from_text("5. Appointment") == []


def test_deadline_extraction():
    text = "The bank shall inform RBI within 10 working days from the date of vacancy."
    (o,) = extract_from_text(text)
    assert o.deadline_text and "10" in o.deadline_text and "days" in o.deadline_text


def test_severity_critical_with_deadline():
    text = "The bank shall inform RBI within 10 working days from the date of vacancy."
    (o,) = extract_from_text(text)
    assert o.severity.value == "critical"


def test_severity_medium_for_should():
    text = "The bank should review the complaints periodically for improvement."
    out = extract_from_text(text)
    assert out and out[0].severity.value == "medium"


def test_unknown_actor_stays_unknown_with_warning():
    text = "The mechanism shall be reviewed at least once in every year for effectiveness."
    out = extract_from_text(text)
    assert out and out[0].responsible_party is None
    assert any("Responsible party" in w for w in out[0].warnings)


def test_applicability_threshold_captured():
    text = "These Directions shall be applicable to Commercial Banks having 10 or more banking outlets in India."
    out = extract_from_text(text)
    assert out and any("10 or more" in c for c in out[0].applicability_conditions)


def test_deterministic_same_input_same_output():
    text = "Every bank shall appoint at least one IO. The bank shall inform RBI within 10 working days."
    first = [(o.quoted_text, o.extraction_confidence) for o in extract_from_text(text)]
    second = [(o.quoted_text, o.extraction_confidence) for o in extract_from_text(text)]
    assert first == second


def test_max_results_bounded():
    text = " ".join(f"The bank shall perform duty number {i}." for i in range(50))
    out = extract_from_text(text, max_results=5)
    assert len(out) == 5
