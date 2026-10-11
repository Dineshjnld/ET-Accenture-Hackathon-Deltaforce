"""Optional local OCR support.

Requires the Tesseract binary (https://github.com/tesseract-ocr/tesseract,
Apache-2.0) and ``pdf2image`` (poppler). Everything degrades gracefully: when
OCR is unavailable the caller is told so and never receives synthetic text.
"""
from __future__ import annotations

import shutil
from functools import lru_cache
from pathlib import Path
from typing import Any

from app.config import settings
from app.logging_config import get_logger

log = get_logger("regulens.ocr")


@lru_cache(maxsize=1)
def ocr_available() -> bool:
    if not settings.ENABLE_OCR:
        return False
    if shutil.which("tesseract") is None:
        return False
    try:
        import pdf2image  # noqa: F401
        import pytesseract  # noqa: F401
    except ImportError:
        return False
    return True


def ocr_status() -> dict[str, Any]:
    binary = shutil.which("tesseract")
    libs = True
    try:
        import pdf2image  # noqa: F401
        import pytesseract  # noqa: F401
    except ImportError:
        libs = False
    return {
        "enabled": settings.ENABLE_OCR,
        "tesseract_path": binary,
        "python_libs": libs,
        "available": bool(binary) and libs and settings.ENABLE_OCR,
        "languages": settings.OCR_LANGUAGES,
    }


def ocr_pdf(path: Path, *, max_pages: int | None = None) -> list[Any]:
    """OCR a PDF locally. Returns ``app.parsing.extractors.ParsedBlock`` objects."""
    import pytesseract
    from pdf2image import convert_from_path

    from app.parsing.extractors import ParsedBlock

    poppler = shutil.which("pdftoppm") or shutil.which("pdftocairo")
    images = convert_from_path(
        str(path),
        dpi=200,
        first_page=1,
        last_page=max_pages or settings.OCR_PAGE_LIMIT,
        poppler_path=str(Path(poppler).parent) if poppler else None,
    )
    blocks: list[Any] = []
    for i, image in enumerate(images, start=1):
        try:
            text = pytesseract.image_to_string(image, lang=settings.OCR_LANGUAGES)
        except Exception as exc:  # pragma: no cover - environment dependent
            log.warning("ocr.page_failed", page=i, error=str(exc))
            continue
        if text.strip():
            blocks.append(ParsedBlock(text=text, page=i, kind="page"))
    return blocks
