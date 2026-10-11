"""Regulatory version comparison and change detection.

The differ segments each version into numbered *provisions*, aligns them across
versions (by clause label first, then by text similarity), and classifies each
difference. A textual difference is **never** automatically turned into a new
legal obligation: anything below the confidence floor is recorded with
``change_type = requires_human_interpretation`` and ``requires_human_review = true``.
"""
from __future__ import annotations

import difflib
import re
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.logging_config import get_logger
from app.models.core import ChangeType, DocumentSourceKind, ReviewStatus
from app.models.obligations import RegulatoryChange
from app.models.regulatory import Document, DocumentVersion, RegulationVersion

log = get_logger("regulens.diff")

_CLAUSE_RE = re.compile(
    r"^\s*(?P<label>(?:\(?\d{1,3}[\.\)])|(?:\(?[ivxlcdm]{1,7}[\.\)])|(?:[A-Z][\.\)])|(?:Chapter\s+[IVXLCDM]+))\s+"
)
_HEADING_RE = re.compile(r"^\s*(?:Chapter\s+[IVXLCDM]+|[0-9]{1,2}\.[0-9]{1,2}\.?)\s*[A-Z][A-Za-z ,\-]{4,80}$")
_DEADLINE_TOKEN_RE = re.compile(
    r"(\d{1,3}\s*(?:working\s+)?days?|\d{1,2}\s*(?:month|year)s?|immediately|quarterly|monthly|annually|"
    r"not\s+later\s+than[^\n,.;]{0,40}|before\s+[A-Z][a-z]+\s+\d{1,2},?\s+\d{4})",
    re.I,
)
_THRESHOLD_RE = re.compile(r"(\b\d{1,7}(?:,\d{3})*(?:\.\d+)?\b)", re.I)
_CONFIDENCE_FLOOR = 0.62


@dataclass
class Provision:
    key: str
    label: str | None
    heading: str | None
    text: str
    index: int


@dataclass
class ChangeCandidate:
    change_type: ChangeType
    clause_ref: str | None
    summary: str
    added_text: str | None
    removed_text: str | None
    confidence: float
    requires_human_review: bool
    source_reference: dict[str, Any]
    warnings: list[str] = field(default_factory=list)


@dataclass
class ComparisonResult:
    left_version_id: str
    right_version_id: str
    left_label: str
    right_label: str
    provisions_left: int
    provisions_right: int
    matched: int
    changes: list[ChangeCandidate]
    summary: dict[str, Any]
    notes: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Segmentation
# ---------------------------------------------------------------------------
def segment_provisions(text: str) -> list[Provision]:
    provisions: list[Provision] = []
    current_key: str | None = None
    current_label: str | None = None
    current_heading: str | None = None
    buffer: list[str] = []

    def flush() -> None:
        nonlocal buffer
        body = "\n".join(buffer).strip()
        if body and current_key is not None:
            provisions.append(
                Provision(
                    key=current_key,
                    label=current_label,
                    heading=current_heading,
                    text=body,
                    index=len(provisions),
                )
            )
        buffer = []

    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        if _HEADING_RE.match(line):
            flush()
            current_heading = line
            current_key = f"ch:{normalise(line)}"
            current_label = None
            continue
        m = _CLAUSE_RE.match(line)
        if m:
            flush()
            current_label = m.group("label").strip("()")
            current_heading = current_heading or (line[:60] if len(line) < 80 else None)
            current_key = f"c:{current_label}"
            buffer.append(line)
            continue
        if current_key is None:
            current_key = "preamble"
            current_label = None
            current_heading = "Preamble"
        buffer.append(line)
    flush()
    return provisions


def normalise(text: str) -> str:
    text = re.sub(r"\s+", " ", (text or "").strip().lower())
    return re.sub(r"[^a-z0-9 ]", "", text)


# ---------------------------------------------------------------------------
# Comparison
# ---------------------------------------------------------------------------
def compare_texts(
    left_text: str,
    right_text: str,
    *,
    left_label: str,
    right_label: str,
    right_is_partial: bool = False,
) -> list[ChangeCandidate]:
    left = segment_provisions(left_text)
    right = segment_provisions(right_text)
    changes: list[ChangeCandidate] = []
    skipped_boilerplate = 0
    out_of_scope = 0

    # Heading-scoped index: clause labels such as "(1)" repeat across chapters,
    # so a bare label is NOT a unique key. Match (heading, label) first.
    right_by_scope: dict[tuple[str, str | None], list[Provision]] = {}
    for p in right:
        right_by_scope.setdefault(_scope(p), []).append(p)
    matched_right: set[int] = set()

    def scope_match(lp: Provision) -> Provision | None:
        cands = [p for p in right_by_scope.get(_scope(lp), []) if p.index not in matched_right]
        if not cands:
            return None
        if len(cands) == 1:
            return cands[0]
        # same (heading, label) occurs more than once: pick the most similar text
        return max(cands, key=lambda p: difflib.SequenceMatcher(None, normalise(lp.text), normalise(p.text)).ratio())

    def label_match(lp: Provision) -> Provision | None:
        if not lp.label:
            return None
        cands = [
            p for p in right
            if p.index not in matched_right and p.label == lp.label
        ]
        if not cands:
            return None
        return max(cands, key=lambda p: difflib.SequenceMatcher(None, normalise(lp.text), normalise(p.text)).ratio())

    for lp in left:
        rp = scope_match(lp)
        aligned = rp is not None
        if rp is None:
            # same clause number under a different heading: accept only when the
            # texts are plausibly related, otherwise fall through to fuzzy match
            cand = label_match(lp)
            if cand is not None:
                sim = difflib.SequenceMatcher(None, normalise(lp.text), normalise(cand.text)).ratio()
                rp = cand if sim >= 0.30 else None
                aligned = rp is not None
        if rp is not None:
            matched_right.add(rp.index)
            changes.extend(_diff_pair(lp, rp, left_label, right_label, aligned_by_clause=aligned))
        else:
            best, score = _best_match(lp, right, matched_right)
            if best is not None and score >= 0.35:
                matched_right.add(best.index)
                changes.extend(_diff_pair(lp, best, left_label, right_label, match_score=score))
            elif right_is_partial:
                # A partial instrument (e.g. an amendment) only speaks to the
                # clauses it lists. Unmatched provisions are OUT OF SCOPE, not
                # removals — asserting removal would be a false signal.
                out_of_scope += 1
            elif _significant(lp.text):
                changes.append(
                    ChangeCandidate(
                        change_type=ChangeType.REMOVED,
                        clause_ref=lp.label or lp.key,
                        summary=f"Provision '{lp.label or lp.key}' present in {left_label} could not be "
                        f"located in {right_label}.",
                        added_text=None,
                        removed_text=lp.text[:4000],
                        confidence=0.6,
                        requires_human_review=True,
                        source_reference={
                            "left_label": left_label,
                            "right_label": right_label,
                            "left_provision": lp.label or lp.key,
                            "left_heading": lp.heading,
                        },
                        warnings=[
                            "Removal is inferred from failing to match the provision; confirm the clause "
                            "was not merely renumbered."
                        ],
                    )
                )
            else:
                skipped_boilerplate += 1

    for rp in right:
        if rp.index in matched_right:
            continue
        if not _significant(rp.text):
            skipped_boilerplate += 1
            continue
        # A right provision closely resembling an already-matched left provision
        # is likely a renumbered duplicate or a split paragraph — do not report
        # it as a new requirement.
        _, best_left = _best_match(rp, left, set())
        if best_left >= 0.50:
            skipped_boilerplate += 1
            continue
        changes.append(
            ChangeCandidate(
                change_type=ChangeType.ADDED,
                clause_ref=rp.label or rp.key,
                summary=f"Provision '{rp.label or rp.key}' appears in {right_label} with no counterpart "
                f"in {left_label}.",
                added_text=rp.text[:4000],
                removed_text=None,
                confidence=0.7,
                requires_human_review=True,
                source_reference={
                    "left_label": left_label,
                    "right_label": right_label,
                    "right_provision": rp.label or rp.key,
                    "right_heading": rp.heading,
                },
                warnings=["New provision — verify whether it creates a genuinely new obligation."],
            )
        )

    if out_of_scope:
        changes.append(
            ChangeCandidate(
                change_type=ChangeType.AMBIGUOUS,
                clause_ref=None,
                summary=f"{out_of_scope} provision(s) of {left_label} are outside the scope of this partial "
                f"instrument ({right_label}) and were NOT assessed as removals.",
                added_text=None,
                removed_text=None,
                confidence=0.9,
                requires_human_review=False,
                source_reference={"left_label": left_label, "right_label": right_label},
                warnings=[],
            )
        )
    if skipped_boilerplate:
        changes.append(
            ChangeCandidate(
                change_type=ChangeType.AMBIGUOUS,
                clause_ref=None,
                summary=f"{skipped_boilerplate} short boilerplate fragment(s) (headers, footers, page furniture) "
                f"were excluded from change detection and are listed here instead of as removals/additions.",
                added_text=None,
                removed_text=None,
                confidence=0.9,
                requires_human_review=False,
                source_reference={"left_label": left_label, "right_label": right_label},
                warnings=[],
            )
        )
    return changes


def _scope(p: Provision) -> tuple[str, str | None]:
    return (normalise(p.heading or ""), p.label)


_SIGNIFICANT_RE = re.compile(r"\b(shall|must|should|within|applicab|appoint|inform|submit|report|retain|review)\b", re.I)


def _significant(text: str) -> bool:
    """A provision is significant if it is long enough or contains directive language."""
    if len(text or "") >= 120:
        return True
    return bool(_SIGNIFICANT_RE.search(text or ""))

    for rp in right:
        if rp.index in matched_right:
            continue
        changes.append(
            ChangeCandidate(
                change_type=ChangeType.ADDED,
                clause_ref=rp.label or rp.key,
                summary=f"Provision '{rp.label or rp.key}' appears in {right_label} with no counterpart "
                f"in {left_label}.",
                added_text=rp.text[:4000],
                removed_text=None,
                confidence=0.7,
                requires_human_review=True,
                source_reference={
                    "left_label": left_label,
                    "right_label": right_label,
                    "right_provision": rp.label or rp.key,
                    "right_heading": rp.heading,
                },
                warnings=["New provision — verify whether it creates a genuinely new obligation."],
            )
        )

    return changes


def _best_match(prov: Provision, candidates: list[Provision], exclude: set[int]) -> tuple[Provision | None, float]:
    best: Provision | None = None
    best_score = 0.0
    target = normalise(prov.text)
    for cand in candidates:
        if cand.index in exclude:
            continue
        score = difflib.SequenceMatcher(None, target, normalise(cand.text)).ratio()
        if score > best_score:
            best, best_score = cand, score
    return (best, best_score) if best is not None else (None, 0.0)


def _diff_pair(
    left: Provision,
    right: Provision,
    left_label: str,
    right_label: str,
    match_score: float | None = None,
    aligned_by_clause: bool = False,
) -> list[ChangeCandidate]:
    out: list[ChangeCandidate] = []
    l_sentences = _sentences(left.text)
    r_sentences = _sentences(right.text)
    if not l_sentences and not r_sentences:
        return out

    sm = difflib.SequenceMatcher(None, [normalise(s) for s in l_sentences], [normalise(s) for s in r_sentences])
    added: list[str] = []
    removed: list[str] = []
    modified_pairs: list[tuple[str, str]] = []

    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal":
            continue
        if tag == "delete":
            removed.extend(l_sentences[i1:i2])
        elif tag == "insert":
            added.extend(r_sentences[j1:j2])
        else:
            l_chunk = " ".join(l_sentences[i1:i2])
            r_chunk = " ".join(r_sentences[j1:j2])
            ratio = difflib.SequenceMatcher(None, normalise(l_chunk), normalise(r_chunk)).ratio()
            if ratio > 0.55:
                modified_pairs.append((l_chunk, r_chunk))
            else:
                removed.append(l_chunk)
                added.append(r_chunk)

    base_ref = {
        "left_label": left_label,
        "right_label": right_label,
        "left_provision": left.label or left.key,
        "right_provision": right.label or right.key,
        "left_heading": left.heading,
        "right_heading": right.heading,
        "provision_match_score": round(match_score, 3) if match_score is not None else 1.0,
    }

    for l_text, r_text in modified_pairs:
        change_type = ChangeType.MODIFIED
        notes = _classify_specific_change(l_text, r_text)
        if notes:
            change_type = notes
        out.append(
            ChangeCandidate(
                change_type=change_type,
                clause_ref=left.label or right.label or left.key,
                summary=_summary_for(change_type, l_text, r_text, left_label, right_label),
                added_text=r_text[:4000],
                removed_text=l_text[:4000],
                confidence=round(min(0.95, 0.55 + (match_score or 1.0) * 0.4), 3),
                requires_human_review=(match_score is not None and match_score < _CONFIDENCE_FLOOR + 0.1)
                or change_type == ChangeType.AMBIGUOUS,
                source_reference=base_ref,
                warnings=_warnings_for(change_type, match_score),
            )
        )

    if added and not modified_pairs and not (aligned_by_clause and removed):
        out.append(
            ChangeCandidate(
                change_type=ChangeType.ADDED,
                clause_ref=left.label or right.label or left.key,
                summary=f"New requirement(s) added to '{left.label or left.key}' in {right_label}.",
                added_text="\n".join(added)[:4000],
                removed_text=None,
                confidence=0.75,
                requires_human_review=True,
                source_reference=base_ref,
                warnings=["Addition detected within an existing provision — confirm legal effect."],
            )
        )
    if removed and not modified_pairs:
        if aligned_by_clause:
            # Same clause number, substantially rewritten: ONE candidate, not a
            # remove+add pair. The rewrite may or may not change legal effect.
            out.append(
                ChangeCandidate(
                    change_type=ChangeType.MODIFIED,
                    clause_ref=left.label or right.label or right.key,
                    summary=(
                        f"Provision '{left.label or left.key}' was substantially rewritten between "
                        f"{left_label} and {right_label}. Left: {_clip(' '.join(removed))} | Right: "
                        f"{_clip(' '.join(added)) if added else '—'}"
                    ),
                    added_text="\n".join(added)[:4000] if added else right.text[:4000],
                    removed_text="\n".join(removed)[:4000],
                    confidence=0.55,
                    requires_human_review=True,
                    source_reference=base_ref,
                    warnings=[
                        "The provision kept its clause number but the wording changed substantially. "
                        "A human must determine whether the legal obligation changed."
                    ],
                )
            )
        else:
            out.append(
                ChangeCandidate(
                    change_type=ChangeType.REMOVED,
                    clause_ref=left.label or right.label or right.key,
                    summary=f"Requirement(s) removed from '{left.label or left.key}' in {right_label}.",
                    added_text=None,
                    removed_text="\n".join(removed)[:4000],
                    confidence=0.72,
                    requires_human_review=True,
                    source_reference=base_ref,
                    warnings=["Removal within an existing provision — confirm the obligation truly ceased."],
                )
            )
    return out


def _classify_specific_change(l_text: str, r_text: str) -> ChangeType | None:
    l_deadline = _DEADLINE_TOKEN_RE.findall(l_text)
    r_deadline = _DEADLINE_TOKEN_RE.findall(r_text)
    if _norm_set(l_deadline) != _norm_set(r_deadline):
        return ChangeType.DEADLINE_CHANGE
    l_thr = _THRESHOLD_RE.findall(l_text)
    r_thr = _THRESHOLD_RE.findall(r_text)
    if _norm_set(l_thr) != _norm_set(r_thr):
        return ChangeType.THRESHOLD_CHANGE
    if re.search(r"applicab|applies\s+to|shall\s+apply", l_text, re.I) or re.search(
        r"applicab|applies\s+to|shall\s+apply", r_text, re.I
    ):
        return ChangeType.APPLICABILITY_CHANGE
    return None


def _summary_for(change_type: ChangeType, l_text: str, r_text: str, left_label: str, right_label: str) -> str:
    head = {
        ChangeType.MODIFIED: "Requirement wording changed between versions.",
        ChangeType.DEADLINE_CHANGE: "Stated deadline/timeframe changed.",
        ChangeType.THRESHOLD_CHANGE: "Numeric threshold or definition changed.",
        ChangeType.APPLICABILITY_CHANGE: "Applicability condition changed.",
    }.get(change_type, "Requirement changed.")
    return (
        f"{head} Left: {_clip(l_text)} | Right: {_clip(r_text)}"
    )


def _warnings_for(change_type: ChangeType, match_score: float | None) -> list[str]:
    warnings: list[str] = []
    if match_score is not None and match_score < _CONFIDENCE_FLOOR + 0.1:
        warnings.append(
            f"Provisions were aligned by text similarity ({match_score:.2f}), not by clause number. "
            "A human must confirm the alignment before relying on this change."
        )
    if change_type in (ChangeType.DEADLINE_CHANGE, ChangeType.THRESHOLD_CHANGE, ChangeType.APPLICABILITY_CHANGE):
        warnings.append(
            "Automated classification of this change type is heuristic — verify against the source text."
        )
    return warnings


def _clip(text: str, limit: int = 220) -> str:
    text = re.sub(r"\s+", " ", text).strip()
    return text if len(text) <= limit else text[: limit - 3] + "..."


def _norm_set(items: list[str]) -> set[str]:
    return {normalise(i) for i in items}


def _sentences(text: str) -> list[str]:
    parts = re.split(r"(?<=[.!?])\s+", text)
    return [re.sub(r"\s+", " ", p).strip() for p in parts if p.strip()]


# ---------------------------------------------------------------------------
# Service API
# ---------------------------------------------------------------------------
def _version_text(session: Session, document_version_id: str | None, document_id: str | None) -> str:
    if document_version_id:
        dv = session.get(DocumentVersion, document_version_id)
        if dv and dv.text_path:
            try:
                return _read(dv.text_path)
            except OSError:  # pragma: no cover
                pass
        if dv:
            chunks = session.scalars(
                select(_chunk_cls()).where(_chunk_cls().document_version_id == document_version_id)
                .order_by(_chunk_cls().chunk_index)
            ).all()
            return "\n".join(c.text for c in chunks)
    if document_id:
        chunks = session.scalars(
            select(_chunk_cls()).where(_chunk_cls().document_id == document_id).order_by(_chunk_cls().chunk_index)
        ).all()
        return "\n".join(c.text for c in chunks)
    return ""


def _read(path: str) -> str:
    from pathlib import Path

    return Path(path).read_text(encoding="utf-8")


def _chunk_cls():
    from app.models.regulatory import DocumentChunk

    return DocumentChunk


def compare_versions(
    session: Session,
    *,
    regulation_id: str,
    from_version_id: str,
    to_version_id: str,
    actor: str = "diff_engine",
    correlation_id: str | None = None,
) -> tuple[ComparisonResult, list[RegulatoryChange]]:
    from app.services.audit import _dump, record_audit

    left = session.get(RegulationVersion, from_version_id)
    right = session.get(RegulationVersion, to_version_id)
    if left is None or right is None:
        raise ValueError("Both regulation versions must exist")

    left_text = _version_text(session, None, left.document_id)
    right_text = _version_text(session, None, right.document_id)
    if not left_text or not right_text:
        raise ValueError("Both versions must have extractable text")

    # A partial instrument (amendment, corrigendum) lists only the clauses it
    # touches. Comparing a full version against one must not report every
    # untouched clause as removed.
    PARTIAL_TYPES = {"simulated_amendment", "amendment", "corrigendum", "circular_amendment"}
    right_doc = right.document
    right_is_partial = bool(right_doc is not None and (right_doc.document_type or "") in PARTIAL_TYPES)

    candidates = compare_texts(
        left_text,
        right_text,
        left_label=left.version_label,
        right_label=right.version_label,
        right_is_partial=right_is_partial,
    )
    left_provisions = segment_provisions(left_text)
    right_provisions = segment_provisions(right_text)

    stored: list[RegulatoryChange] = []
    for cand in candidates:
        change = RegulatoryChange(
            change_uid=_next_change_uid(session),
            regulation_id=regulation_id,
            from_version_id=from_version_id,
            to_version_id=to_version_id,
            change_type=cand.change_type,
            summary=cand.summary,
            added_text=cand.added_text,
            removed_text=cand.removed_text,
            source_reference=_dump(cand.source_reference),
            clause_ref=cand.clause_ref,
            detected_by="diff_engine",
            confidence=cand.confidence,
            requires_human_review=cand.requires_human_review,
            review_status=ReviewStatus.PENDING,
            is_synthetic=(left.document.source_kind == DocumentSourceKind.SYNTHETIC)
            if left.document
            else False,
        )
        session.add(change)
        stored.append(change)
    session.flush()

    record_audit(
        session,
        actor=actor,
        action="regulation.versions_compared",
        entity_type="regulation",
        entity_id=regulation_id,
        entity_uid=right.version_label,
        correlation_id=correlation_id,
        after={
            "from": left.version_label,
            "to": right.version_label,
            "changes": len(stored),
            "by_type": _counts(stored),
        },
    )

    result = ComparisonResult(
        left_version_id=from_version_id,
        right_version_id=to_version_id,
        left_label=left.version_label,
        right_label=right.version_label,
        provisions_left=len(left_provisions),
        provisions_right=len(right_provisions),
        matched=len(left_provisions) - sum(1 for c in stored if c.change_type == ChangeType.REMOVED),
        changes=candidates,
        summary={
            "changes": len(stored),
            "by_type": _counts(stored),
            "requires_human_review": sum(1 for c in stored if c.requires_human_review),
            "note": "Changes are candidate signals for review. They are not determinations of legal effect.",
        },
    )
    return result, stored


def _counts(changes: list[RegulatoryChange]) -> dict[str, int]:
    out: dict[str, int] = {}
    for c in changes:
        key = str(c.change_type)
        out[key] = out.get(key, 0) + 1
    return out


def _next_change_uid(session: Session) -> str:
    from datetime import datetime
    import secrets

    for _ in range(10):
        candidate = f"CHG-{datetime.utcnow().strftime('%y%m%d')}-{secrets.token_hex(3).upper()}"
        if not session.scalar(
            select(RegulatoryChange.id).where(RegulatoryChange.change_uid == candidate).limit(1)
        ):
            return candidate
    return f"CHG-{secrets.token_hex(8).upper()}"  # pragma: no cover


__all__ = ["compare_texts", "compare_versions", "segment_provisions", "Provision", "ChangeCandidate", "ComparisonResult"]
