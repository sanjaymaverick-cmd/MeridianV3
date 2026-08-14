"""Image / screenshot portfolio extraction.

Uses pytesseract when installed. Always falls back to a structured
regex pass so the desk still works without Tesseract on the machine.

The user must review every extracted row before commit.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from meridian_v3.ingestion.columns import clean_header

_LINE = re.compile(
    r"""
    (?P<name>[A-Z][A-Z0-9&.\-]{1,24}(?:\s+[A-Z][A-Z0-9&.\-]{1,24}){0,4})
    \s+
    (?P<qty>[\d,]+(?:\.\d+)?)
    \s+
    (?P<avg>[\d,]+(?:\.\d+)?)
    (?:\s+(?P<ltp>[\d,]+(?:\.\d+)?))?
    \s*$
    """,
    re.VERBOSE,
)


def extract_text(path: Path) -> str:
    try:
        from PIL import Image
        import pytesseract
    except ImportError:
        return _fallback_note(path)
    try:
        image = Image.open(path)
        return pytesseract.image_to_string(image) or ""
    except Exception:  # noqa: BLE001
        return _fallback_note(path)


def _fallback_note(path: Path) -> str:
    return (
        f"[ocr-unavailable] {path.name}. Install Tesseract and pip install 'meridian-v3[ocr]' "
        "for screenshot statements. You can still type the rows or use PDF / Excel."
    )


def text_to_records(text: str) -> tuple[list[str], list[dict[str, Any]]]:
    headers = ["stock name", "shares", "avg buy price", "current price"]
    records: list[dict[str, Any]] = []
    for raw in text.splitlines():
        line = clean_header(raw).upper()
        if not line or line.startswith("["):
            continue
        match = _LINE.search(raw.upper())
        if not match:
            continue
        records.append(
            {
                "stock name": match.group("name").strip(),
                "shares": match.group("qty"),
                "avg buy price": match.group("avg"),
                "current price": match.group("ltp"),
            }
        )
    return headers, records


def read_image(path: Path) -> tuple[list[str], list[dict[str, Any]], str]:
    text = extract_text(path)
    headers, records = text_to_records(text)
    note = "OCR extracted these rows. Please check every line before you import."
    if text.startswith("[ocr-unavailable]"):
        note = text
    return headers, records, note
