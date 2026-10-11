"""Local document parsing. No network, no paid parsing service."""
from __future__ import annotations

from app.parsing.ocr import ocr_available, ocr_pdf, ocr_status
from app.parsing.extractors import (
    ParsedBlock,
    ParsedDocument,
    ParseError,
    parse_document,
)

__all__ = [
    "ParsedBlock",
    "ParsedDocument",
    "ParseError",
    "parse_document",
    "ocr_available",
    "ocr_pdf",
    "ocr_status",
]
