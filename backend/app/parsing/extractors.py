"""Document text extraction (PDF/DOCX/TXT/HTML/CSV/JSON).

All parsers are pure-Python and run locally. No network access, no paid
parsing service. OCR is an optional hook (see :mod:`app.parsing.ocr`).
"""
from __future__ import annotations

import csv
import io
import json
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

from pypdf import PdfReader

from app.logging_config import get_logger

log = get_logger("regulens.parsing")


class ParseError(Exception):
    """Raised when a document cannot be parsed."""


@dataclass
class ParsedBlock:
    """A contiguous text block with positional provenance."""

    text: str
    page: int | None = None
    section: str | None = None
    heading: str | None = None
    kind: str = "paragraph"


@dataclass
class ParsedDocument:
    text: str
    blocks: list[ParsedBlock] = field(default_factory=list)
    page_count: int = 0
    metadata: dict[str, Any] = field(default_factory=dict)
    ocr_used: bool = False
    warnings: list[str] = field(default_factory=list)


# --------------------------------------------------------------------------
# PDF
# --------------------------------------------------------------------------
def parse_pdf(path: Path, *, max_pages: int | None = None) -> ParsedDocument:
    warnings: list[str] = []
    try:
        reader = PdfReader(str(path))
    except Exception as exc:
        raise ParseError(f"Unreadable PDF: {exc}") from exc

    if reader.is_encrypted:
        try:
            reader.decrypt("")
        except Exception as exc:  # pragma: no cover
            raise ParseError("PDF is password protected and cannot be read") from exc

    pages = reader.pages
    page_count = len(pages)
    limit = min(page_count, max_pages) if max_pages else page_count
    blocks: list[ParsedBlock] = []
    for idx in range(limit):
        page = pages[idx]
        try:
            text = page.extract_text() or ""
        except Exception as exc:  # pragma: no cover - malformed pdf
            warnings.append(f"page {idx + 1}: extraction error ({exc})")
            text = ""
        if text.strip():
            blocks.append(ParsedBlock(text=text, page=idx + 1, kind="page"))

    total_chars = sum(len(b.text) for b in blocks)
    ocr_used = False
    if total_chars < 40 and limit > 0:
        from app.parsing.ocr import ocr_available, ocr_pdf

        if ocr_available():
            try:
                ocr_blocks = ocr_pdf(path, max_pages=limit)
                if ocr_blocks:
                    blocks = ocr_blocks
                    ocr_used = True
                    warnings.append("Text layer absent; content recovered via local OCR (tesseract).")
            except Exception as exc:  # pragma: no cover - environment dependent
                warnings.append(f"OCR attempted but failed: {exc}")
        else:
            warnings.append(
                "PDF has no extractable text layer and OCR is unavailable "
                "(install Tesseract + set ENABLE_OCR=true)."
            )

    meta = _pdf_metadata(reader)
    return ParsedDocument(
        text="\n\n".join(b.text for b in blocks),
        blocks=blocks,
        page_count=page_count,
        metadata=meta,
        ocr_used=ocr_used,
        warnings=warnings,
    )


def _pdf_metadata(reader: PdfReader) -> dict[str, Any]:
    try:
        raw = reader.metadata or {}
    except Exception:  # pragma: no cover
        raw = {}
    out: dict[str, Any] = {}
    for key, value in raw.items():
        k = str(key).lstrip("/")
        out[k] = str(value)
    return out


# --------------------------------------------------------------------------
# DOCX
# --------------------------------------------------------------------------
def parse_docx(path: Path) -> ParsedDocument:
    try:
        import docx
    except ImportError as exc:  # pragma: no cover
        raise ParseError("python-docx is not installed") from exc
    try:
        document = docx.Document(str(path))
    except Exception as exc:
        raise ParseError(f"Unreadable DOCX: {exc}") from exc

    blocks: list[ParsedBlock] = []
    for para in document.paragraphs:
        text = para.text.strip()
        if not text:
            continue
        style = (para.style.name or "").lower()
        kind = "heading" if style.startswith("heading") or style.startswith("title") else "paragraph"
        blocks.append(ParsedBlock(text=text, kind=kind))

    for table in document.tables:
        rows = []
        for row in table.rows:
            rows.append(" | ".join(cell.text.strip() for cell in row.cells))
        if rows:
            blocks.append(ParsedBlock(text="\n".join(rows), kind="table"))

    core = document.core_properties
    meta: dict[str, Any] = {}
    for attr in ("title", "author", "subject", "created", "modified", "last_modified_by", "revision"):
        val = getattr(core, attr, None)
        if val:
            meta[attr] = val.isoformat() if isinstance(val, (date,)) else str(val)
    return ParsedDocument(text="\n\n".join(b.text for b in blocks), blocks=blocks, metadata=meta)


# --------------------------------------------------------------------------
# Plain text / markdown
# --------------------------------------------------------------------------
def parse_text(path: Path) -> ParsedDocument:
    raw = path.read_bytes()
    text = _decode(raw)
    return ParsedDocument(text=text, blocks=[ParsedBlock(text=text, kind="text")])


def _decode(raw: bytes) -> str:
    for enc in ("utf-8", "utf-16", "cp1252", "latin-1"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")  # pragma: no cover


# --------------------------------------------------------------------------
# HTML
# --------------------------------------------------------------------------
def parse_html(path: Path) -> ParsedDocument:
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(_decode(path.read_bytes()), "lxml")
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()
    title = soup.title.get_text(strip=True) if soup.title else None
    blocks: list[ParsedBlock] = []
    for element in soup.find_all(["h1", "h2", "h3", "h4", "p", "li", "tr"]):
        text = element.get_text(" ", strip=True)
        if not text:
            continue
        name = element.name
        kind = "heading" if name in ("h1", "h2", "h3", "h4") else "table" if name == "tr" else "paragraph"
        blocks.append(ParsedBlock(text=text, kind=kind))
    meta = {"title": title} if title else {}
    return ParsedDocument(text="\n\n".join(b.text for b in blocks), blocks=blocks, metadata=meta)


# --------------------------------------------------------------------------
# CSV / JSON
# --------------------------------------------------------------------------
def parse_csv(path: Path) -> ParsedDocument:
    text = _decode(path.read_bytes())
    reader = csv.reader(io.StringIO(text))
    rows = [row for row in reader if any(cell.strip() for cell in row)]
    blocks: list[ParsedBlock] = []
    if rows:
        header = rows[0]
        for i, row in enumerate(rows[1:], start=1):
            pairs = [f"{h}: {v}" for h, v in zip(header, row) if v.strip()]
            blocks.append(ParsedBlock(text="\n".join(pairs), section=f"row {i}", kind="row"))
    meta = {"row_count": max(0, len(rows) - 1), "columns": rows[0] if rows else []}
    return ParsedDocument(
        text="\n\n".join(b.text for b in blocks), blocks=blocks, metadata=meta
    )


def parse_json(path: Path) -> ParsedDocument:
    text = _decode(path.read_bytes())
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ParseError(f"Invalid JSON: {exc}") from exc
    blocks: list[ParsedBlock] = []

    def walk(node: Any, prefix: str) -> None:
        if isinstance(node, dict):
            for key, value in node.items():
                walk(value, f"{prefix}.{key}" if prefix else str(key))
        elif isinstance(node, list):
            for i, value in enumerate(node):
                walk(value, f"{prefix}[{i}]")
        elif isinstance(node, (str, int, float, bool)) or node is None:
            blocks.append(
                ParsedBlock(text=f"{prefix}: {json.dumps(node, ensure_ascii=False)}", section=prefix, kind="field")
            )

    walk(data, "")
    if len(blocks) > 4000:
        blocks = blocks[:4000]
    return ParsedDocument(
        text="\n\n".join(b.text for b in blocks),
        blocks=blocks,
        metadata={"key_count": len(blocks)},
    )


# --------------------------------------------------------------------------
# Dispatch
# --------------------------------------------------------------------------
_PARSERS = {
    ".pdf": parse_pdf,
    ".docx": parse_docx,
    ".txt": parse_text,
    ".md": parse_text,
    ".html": parse_html,
    ".htm": parse_html,
    ".csv": parse_csv,
    ".json": parse_json,
}


def parse_document(path: Path) -> ParsedDocument:
    suffix = path.suffix.lower()
    parser = _PARSERS.get(suffix)
    if parser is None:
        raise ParseError(f"Unsupported file type '{suffix}'")
    return parser(path)
