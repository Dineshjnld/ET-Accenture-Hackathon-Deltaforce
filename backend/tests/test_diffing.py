"""Version comparison tests."""
from __future__ import annotations

from app.models.core import ChangeType, enum_value
from app.services.diffing import compare_texts, segment_provisions


LEFT = """Chapter II
Office of the Internal Ombudsman
5. Appointment
(1) The IO shall either be a retired officer in equivalent rank.
6. Vacancy
(1) The bank shall inform RBI within 10 working days from the date of vacancy.
7. Number of IOs
(1) Every bank shall appoint at least one IO.
"""

RIGHT = """Chapter II
Office of the Internal Ombudsman
5. Appointment
(1) The IO shall either be a retired officer in equivalent rank. The bank shall complete appointment within 60 days.
6. Vacancy
(1) The bank shall inform RBI within 5 working days from the date of vacancy.
8. Quarterly reporting
(1) The IO shall submit a quarterly report within 15 days of each quarter.
"""


def _types(changes):
    return {enum_value(c.change_type) for c in changes}


def test_modified_and_added_detected():
    changes = compare_texts(LEFT, RIGHT, left_label="v1", right_label="v2")
    types = _types(changes)
    assert "modified" in types or "deadline_change" in types
    assert "added" in types  # clause 8


def test_deadline_change_classified():
    changes = compare_texts(LEFT, RIGHT, left_label="v1", right_label="v2")
    deadline = [c for c in changes if enum_value(c.change_type) == "deadline_change"]
    assert deadline, [c.summary for c in changes]
    assert any("5 working days" in (c.added_text or "") for c in deadline)


def test_identical_texts_produce_no_changes():
    assert compare_texts(LEFT, LEFT, left_label="v1", right_label="v1b") == []


def test_partial_right_does_not_emit_removals():
    right = "6. Vacancy\n(1) The bank shall inform RBI within 5 working days."
    changes = compare_texts(LEFT, right, left_label="v1", right_label="amend", right_is_partial=True)
    assert not [c for c in changes if enum_value(c.change_type) == "removed"]
    assert any("outside the scope" in c.summary for c in changes)


def test_full_comparison_emits_removals_for_missing_clause():
    right = "6. Vacancy\n(1) The bank shall inform RBI within 5 working days from the date of vacancy in all cases."
    changes = compare_texts(LEFT, right, left_label="v1", right_label="v2")
    removed = [c for c in changes if enum_value(c.change_type) == "removed"]
    assert removed  # clause 5 and 7 have no counterpart


def test_clause_aligned_rewrite_is_single_candidate():
    left = "6. Vacancy\n(1) The bank shall inform RBI within 10 working days from the date of vacancy."
    right = "6. Vacancy\n(1) Completely different topic about publishing annual numbers in December."
    changes = compare_texts(left, right, left_label="v1", right_label="v2")
    # same clause number: one MODIFIED candidate, never an add+remove pair
    assert len(changes) == 1
    assert enum_value(changes[0].change_type) == "modified"
    assert changes[0].requires_human_review is True


def test_segment_provisions_heading_scoped():
    provisions = segment_provisions(LEFT)
    assert len(provisions) >= 4
    assert all(p.key for p in provisions)
