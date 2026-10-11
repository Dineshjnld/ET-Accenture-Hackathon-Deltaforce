"""Deterministic obligation extraction from regulatory text.

This is a transparent rule-based extractor. Every extracted obligation keeps the
verbatim source sentence(s) and its position so a human reviewer can audit the
claim. Nothing is invented: when the actor, deadline or severity cannot be
established from the text, the field is left ``None``/``unknown`` and the
record is created with ``review_status = pending``.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Iterable

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.logging_config import get_logger
from app.models.core import DocumentSourceKind, ReviewStatus, Severity, enum_value
from app.models.obligations import Obligation
from app.models.regulatory import Document, DocumentChunk, RegulationVersion

log = get_logger("regulens.obligations")

MODALS = [
    r"shall",
    r"must",
    r"shall\s+not",
    r"may\s+not",
    r"is\s+required\s+to",
    r"are\s+required\s+to",
    r"will\s+be\s+required\s+to",
    r"has\s+to",
    r"have\s+to",
    r"is\s+mandated\s+to",
    r"are\s+mandated\s+to",
    r"is\s+expected\s+to",
    r"are\s+expected\s+to",
    r"should",
    r"ought\s+to",
    r"need\s+to",
    r"needs\s+to",
]
_MODAL_RE = re.compile("|".join(f"(?:{m})" for m in MODALS), re.IGNORECASE)
_STRONG_MODAL_RE = re.compile(r"\b(shall|must|shall\s+not|may\s+not)\b", re.IGNORECASE)
_SOFT_MODAL_RE = re.compile(r"\b(should|ought\s+to|is\s+expected\s+to|are\s+expected\s+to)\b", re.IGNORECASE)
_PERMISSIVE_RE = re.compile(r"\bmay\b(?!\s+not)", re.IGNORECASE)

_DEFINITION_RE = re.compile(
    r"\b(means|refers\s+to|shall\s+mean|is\s+defined\s+as|are\s+defined\s+as|denotes)\b", re.IGNORECASE
)
_APPLICABILITY_RE = re.compile(
    r"\b(applicable\s+to|shall\s+apply\s+to|applies\s+to|applies\s+only\s+to)\b", re.IGNORECASE
)
_CROSSREF_RE = re.compile(r"\b(clause|sub-clause|paragraph|regulation|section|chapter)\s+[0-9ivxlcdm]+", re.IGNORECASE)

_CLAUSE_RE = re.compile(
    r"^\s*(?P<label>(?:\(?\d{1,3}[\.\)])|(?:\(?[ivxlcdm]{1,7}[\.\)])|(?:[A-Z][\.\)])|(?:Chapter\s+[IVXLCDM]+))\s*(?P<rest>.*)$"
)

ACTOR_LEXICON: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"\b(banks?)\b", re.I), "Bank"),
    (re.compile(r"\bregulated\s+entit(?:y|ies)\b|\bREs?\b", re.I), "Regulated Entity"),
    (re.compile(r"\bNBFCs?\b|non-banking\s+financial\s+compan", re.I), "NBFC"),
    (re.compile(r"\bsmall\s+finance\s+banks?\b|\bSFBs?\b", re.I), "Small Finance Bank"),
    (re.compile(r"\bpayment\s+banks?\b|\bPBs?\b", re.I), "Payments Bank"),
    (re.compile(r"\bco-operative\s+banks?\b|\bUCBs?\b|\bRRBs?\b", re.I), "Co-operative Bank"),
    (re.compile(r"\bInternal\s+Ombudsman\b|\bIO\b|\bDy\.?\s?IO\b", re.I), "Internal Ombudsman"),
    (re.compile(r"\bBoard\b|\bBoard\s+of\s+Directors\b", re.I), "Board of Directors"),
    (re.compile(r"\bCustomer\s+Service\s+Committee\b|\bCSC\b", re.I), "Customer Service Committee"),
    (re.compile(r"\bCompliance\s+(Officer|Department|function)\b", re.I), "Compliance Function"),
    (re.compile(r"\bcomplaint\s+(redressal\s+)?(officer|cell|desk)\b", re.I), "Complaint Redressal Officer"),
    (re.compile(r"\bauditor", re.I), "Auditor"),
]

ACTION_LEXICON: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"\bappoint|nominate|constitut(e|ion)\b", re.I), "Appoint"),
    (re.compile(r"\bsubmit|furnish|send|communicate|forward\b", re.I), "Submit"),
    (re.compile(r"\breport|inform\b", re.I), "Report"),
    (re.compile(r"\bdisclos|publish|display\b", re.I), "Disclose"),
    (re.compile(r"\bmaintain|keep\b", re.I), "Maintain"),
    (re.compile(r"\bverif|authenticat", re.I), "Verify"),
    (re.compile(r"\breview|revisit\b", re.I), "Review"),
    (re.compile(r"\bmonitor\b", re.I), "Monitor"),
    (re.compile(r"\bretain|preserve|record\b", re.I), "Retain records"),
    (re.compile(r"\bimplement|put\s+in\s+place\b", re.I), "Implement"),
    (re.compile(r"\btrain|awareness\b", re.I), "Train"),
    (re.compile(r"\bescalat", re.I), "Escalate"),
    (re.compile(r"\bconduct|undertake|carry\s+out\b", re.I), "Conduct"),
    (re.compile(r"\bensur", re.I), "Ensure"),
    (re.compile(r"\bcomply\b", re.I), "Comply"),
    (re.compile(r"\bobtain\s+(approval|permission)", re.I), "Obtain approval"),
    (re.compile(r"\bexamine|inspect|audit\b", re.I), "Examine"),
    (re.compile(r"\bperiodic(ally)?\s+review", re.I), "Periodically review"),
]

DEADLINE_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"within\s+(\d{1,3})\s+(working\s+)?days?", re.I), r"within \1 \2days"),
    (re.compile(r"within\s+(\d{1,2})\s+(month|year)s?", re.I), r"within \1 \2s"),
    (re.compile(r"(?:not\s+later\s+than|no\s+later\s+than)\s+([A-Z][a-z]+\s+\d{1,2},?\s+\d{4})", re.I), r"not later than \1"),
    (re.compile(r"\bbefore\s+([A-Z][a-z]+\s+\d{1,2},?\s+\d{4})", re.I), r"before \1"),
    (re.compile(r"\bby\s+([A-Z][a-z]+\s+\d{1,2},?\s+\d{4})", re.I), r"by \1"),
    (re.compile(r"\b(at\s+least\s+once\s+(?:a|an|every)\s+(?:year|quarter|month|half))", re.I), r"\1"),
    (re.compile(r"\b(every\s+(?:quarter|month|year|half\s+year))", re.I), r"\1"),
    (re.compile(r"\b(quarterly|monthly|annually|half-yearly|fortnightly)\b", re.I), r"\1"),
    (re.compile(r"\bimmediately\b", re.I), "immediately"),
]

PENALTY_RE = re.compile(
    r"\b(penalt|fine|supervisory\s+action|restrict|prohibit|withdraw|debar|liable|consequence)", re.I
)
_NEGATION_ONLY_RE = re.compile(r"^(?:provided\s+that|nothing\s+in|for\s+the\s+avoidance)", re.I)


@dataclass
class ExtractedObligation:
    obligation_text: str
    quoted_text: str
    responsible_party: str | None
    action_required: str | None
    applicability_conditions: list[str]
    deadline_text: str | None
    effective_date: date | None
    severity: Severity
    severity_rationale: str
    extraction_confidence: float
    source_page: int | None
    source_section: str | None
    clause_label: str | None = None
    warnings: list[str] = field(default_factory=list)


def _sentences(text: str) -> list[str]:
    parts = re.split(r"(?<=[.!?])\s+(?=[A-Z(])", text)
    out: list[str] = []
    for part in parts:
        part = re.sub(r"\s+", " ", part).strip()
        if part:
            out.append(part)
    return out


def _is_obligation_sentence(sentence: str) -> bool:
    if len(sentence) < 25:
        return False
    if len(sentence) > 1200:
        return False
    if _DEFINITION_RE.search(sentence) and not _MODAL_RE.search(sentence):
        return False
    if not _MODAL_RE.search(sentence):
        return False
    if _NEGATION_ONLY_RE.search(sentence):
        # Carve-outs and savings clauses ("Nothing in these Directions shall
        # affect ...") limit scope; they are not standalone obligations.
        return False
    if sentence.lower().startswith(("table of contents", "contents", "index")):
        return False
    return True


def _detect_deadline(sentence: str) -> str | None:
    for pattern, template in DEADLINE_PATTERNS:
        m = pattern.search(sentence)
        if m:
            if "\\1" in template or "\\2" in template:
                try:
                    return m.expand(template)
                except re.error:  # pragma: no cover
                    return m.group(0)
            return template
    return None


def _detect_actor(sentence: str) -> str | None:
    # prefer an actor that appears *before* the modal
    modal = _STRONG_MODAL_RE.search(sentence) or _MODAL_RE.search(sentence)
    window = sentence[: modal.start()] if modal else sentence
    for pattern, label in ACTOR_LEXICON:
        if pattern.search(window):
            return label
    for pattern, label in ACTOR_LEXICON:
        if pattern.search(sentence):
            return label
    return None


def _detect_action(sentence: str) -> str | None:
    for pattern, label in ACTION_LEXICON:
        if pattern.search(sentence):
            return label
    return None


def _detect_applicability(sentence: str) -> list[str]:
    conditions: list[str] = []
    if _APPLICABILITY_RE.search(sentence):
        conditions.append(f"Stated applicability condition: {sentence}")
    for m in re.finditer(r"\b(\d{1,6}(?:,\d{3})*|\d+(?:\.\d+)?)\s*(or\s*more|and\s*above|and\s*more)\b", sentence, re.I):
        conditions.append(f"Threshold stated in text: {m.group(0)}")
    if re.search(r"\b(?:all\s+)?(?:scheduled\s+)?commercial\s+banks\b", sentence, re.I) and "applicable" in sentence.lower():
        conditions.append("Scope stated: commercial banks")
    return conditions


def _score_severity(sentence: str, deadline: str | None) -> tuple[Severity, str]:
    strong = bool(_STRONG_MODAL_RE.search(sentence))
    soft = bool(_SOFT_MODAL_RE.search(sentence))
    penalty = bool(PENALTY_RE.search(sentence))
    if strong and (deadline or penalty):
        return Severity.CRITICAL, "Binding modal ('shall'/'must') combined with a stated deadline or sanction."
    if strong:
        return Severity.HIGH, "Binding modal ('shall'/'must') with no stated deadline or sanction in this sentence."
    if soft:
        return Severity.MEDIUM, "Soft/expectation modal ('should'/'expected to')."
    if _PERMISSIVE_RE.search(sentence):
        return Severity.LOW, "Permissive language ('may') — confirm whether this is a genuine obligation."
    return Severity.UNKNOWN, "Obligation keyword present but modal force could not be classified."


def _confidence(sentence: str, clause_label: str | None, actor: str | None, deadline: str | None) -> float:
    score = 0.30
    score += 0.18 if _STRONG_MODAL_RE.search(sentence) else 0.0
    score += 0.12 if clause_label else 0.0
    score += 0.12 if actor else 0.0
    score += 0.10 if deadline else 0.0
    score += 0.08 if not _CROSSREF_RE.search(sentence) else 0.0
    score += 0.05 if 60 <= len(sentence) <= 700 else 0.0
    return round(min(0.90, score), 3)


def extract_from_text(
    text: str,
    *,
    source_page: int | None = None,
    source_section: str | None = None,
    max_results: int = 200,
) -> list[ExtractedObligation]:
    """Extract atomic obligations from a block of regulatory text."""
    results: list[ExtractedObligation] = []
    current_section = source_section
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        clause_label = None
        m = _CLAUSE_RE.match(line)
        if m and len(m.group("rest")) > 3:
            clause_label = m.group("label").strip("()")
            line = f"{m.group('label')} {m.group('rest')}".strip()

        for sentence in _sentences(line):
            if not _is_obligation_sentence(sentence):
                continue
            deadline = _detect_deadline(sentence)
            actor = _detect_actor(sentence)
            action = _detect_action(sentence)
            applicability = _detect_applicability(sentence)
            severity, rationale = _score_severity(sentence, deadline)
            warnings: list[str] = []
            if actor is None:
                warnings.append("Responsible party could not be identified from the source text.")
            if _CROSSREF_RE.search(sentence):
                warnings.append("Sentence refers to another clause; the full requirement may span locations.")
            if applicability:
                warnings.append("Applicability condition is stated in text and must be validated against the entity profile.")
            results.append(
                ExtractedObligation(
                    obligation_text=_compress(sentence),
                    quoted_text=sentence,
                    responsible_party=actor,
                    action_required=action,
                    applicability_conditions=applicability,
                    deadline_text=deadline,
                    effective_date=None,
                    severity=severity,
                    severity_rationale=rationale,
                    extraction_confidence=_confidence(
                        sentence, clause_label, actor, deadline
                    ),
                    source_page=source_page,
                    source_section=current_section,
                    clause_label=clause_label,
                    warnings=warnings,
                )
            )
            if len(results) >= max_results:
                return results
    return results


def _compress(sentence: str) -> str:
    text = sentence.strip()
    text = re.sub(r"\s+", " ", text)
    return text[:900]


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------
def persist_obligations(
    session: Session,
    *,
    regulation_version: RegulationVersion,
    document: Document,
    extraction_method: str = "deterministic",
    extraction_model: str | None = None,
    limit: int | None = None,
    chunk_ids: list[str] | None = None,
) -> list[Obligation]:
    """Extract obligations from a document's chunks and store them as pending."""
    stmt = select(DocumentChunk).where(DocumentChunk.document_id == document.id).order_by(DocumentChunk.chunk_index)
    if chunk_ids:
        stmt = select(DocumentChunk).where(DocumentChunk.id.in_(chunk_ids)).order_by(DocumentChunk.chunk_index)
    chunks = list(session.scalars(stmt))
    if limit:
        chunks = chunks[:limit]

    created: list[Obligation] = []
    existing_quotes = {
        q.strip()
        for q in session.scalars(
            select(Obligation.quoted_text).where(Obligation.regulation_version_id == regulation_version.id)
        )
    }
    for chunk in chunks:
        for extracted in extract_from_text(
            chunk.text, source_page=chunk.page, source_section=chunk.section
        ):
            if extracted.quoted_text.strip() in existing_quotes:
                continue
            obligation = Obligation(
                obligation_uid=_next_uid(session),
                regulation_id=regulation_version.regulation_id,
                regulation_version_id=regulation_version.id,
                source_document_id=document.id,
                source_document_version_id=chunk.document_version_id,
                source_page=extracted.source_page,
                source_section=extracted.source_section,
                quoted_text=extracted.quoted_text,
                obligation_text=extracted.obligation_text,
                responsible_party=extracted.responsible_party,
                action_required=extracted.action_required,
                applicability_conditions=_json_list(extracted.applicability_conditions),
                effective_date=extracted.effective_date,
                deadline_text=extracted.deadline_text,
                jurisdiction=document.jurisdiction,
                severity=extracted.severity,
                severity_rationale=extracted.severity_rationale,
                extraction_confidence=extracted.extraction_confidence,
                extraction_method=extraction_method,
                extraction_model=extraction_model,
                review_status=ReviewStatus.PENDING,
                is_synthetic=(document.source_kind == DocumentSourceKind.SYNTHETIC),
            )
            session.add(obligation)
            created.append(obligation)
    session.flush()
    if created:
        log.info(
            "obligations.extracted",
            document=document.id,
            method=extraction_method,
            count=len(created),
        )
    return created


def _json_list(items: Iterable[str]) -> str | None:
    import json

    items = list(items)
    return json.dumps(items) if items else None


def _next_uid(session: Session) -> str:
    from datetime import datetime
    import secrets

    from app.models.obligations import Obligation as _O

    for _ in range(10):
        candidate = f"OBL-{datetime.utcnow().strftime('%y%m%d')}-{secrets.token_hex(3).upper()}"
        if not session.scalar(select(_O.id).where(_O.obligation_uid == candidate).limit(1)):
            return candidate
    return f"OBL-{secrets.token_hex(8).upper()}"  # pragma: no cover


# ---------------------------------------------------------------------------
# AI-assisted persistence (validated hosted-model candidates)
# ---------------------------------------------------------------------------
def persist_ai_obligations(
    session: Session,
    items: list[Any],
    *,
    regulation_version: RegulationVersion,
    document: Document,
    provider: str,
    model: str | None,
    source_text: str,
) -> tuple[list[Obligation], list[str]]:
    """Persist Pydantic-validated AI obligation candidates.

    Strict honesty rules: items whose quoted_text is non-empty but NOT verbatim
    in the source are REJECTED (possible fabrication) and reported in warnings.
    Duplicates of existing quotes are skipped. Everything stays pending review.
    """
    from app.llm.schemas import ObligationItem

    created: list[Obligation] = []
    warnings: list[str] = []
    existing_quotes = {
        q.strip()
        for q in session.scalars(
            select(Obligation.quoted_text).where(Obligation.regulation_version_id == regulation_version.id)
        )
    }
    for raw in items:
        item = raw if isinstance(raw, ObligationItem) else ObligationItem.model_validate(raw)
        text = (item.obligation_text or "").strip()
        quote = (item.quoted_text or "").strip()
        if len(text) < 10:
            warnings.append("Rejected an AI candidate with an empty obligation_text.")
            continue
        if quote:
            if quote not in source_text:
                warnings.append(
                    "Rejected an AI candidate whose quoted_text is not verbatim in the source."
                )
                continue
            if quote.strip() in existing_quotes:
                continue
        obligation = Obligation(
            obligation_uid=_next_uid(session),
            regulation_id=regulation_version.regulation_id,
            regulation_version_id=regulation_version.id,
            source_document_id=document.id,
            source_document_version_id=document.versions[0].id if getattr(document, "versions", None) else None,
            source_page=None,
            source_section=None,
            quoted_text=quote or text[:900],
            obligation_text=text[:900],
            responsible_party=item.responsible_party,
            action_required=item.action_required,
            applicability_conditions=_json_list(item.applicability_conditions or []),
            effective_date=None,
            deadline_text=item.deadline_text,
            jurisdiction=document.jurisdiction,
            severity=item.severity,
            severity_rationale=f"AI-suggested severity ({provider}); requires human review.",
            extraction_confidence=float(item.confidence or 0.5),
            extraction_method=provider,
            extraction_model=model,
            review_status=ReviewStatus.PENDING,
            is_synthetic=(document.source_kind == DocumentSourceKind.SYNTHETIC),
        )
        session.add(obligation)
        created.append(obligation)
        existing_quotes.add((quote or text[:900]).strip())
    session.flush()
    if created:
        log.info("obligations.ai_persisted", provider=provider, count=len(created))
    return created, warnings


__all__ = ["extract_from_text", "persist_obligations", "persist_ai_obligations", "ExtractedObligation"]
