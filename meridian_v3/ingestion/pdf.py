from __future__ import annotations

from pathlib import Path
from typing import Any

from meridian_v3.ingestion.columns import FIELD_ALIASES, clean_header


def read_pdf_tables(path: Path) -> tuple[list[str], list[dict[str, Any]]]:
    try:
        import pdfplumber
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError("pdfplumber is required for PDF statements") from exc

    headers: list[str] = []
    records: list[dict[str, Any]] = []
    with pdfplumber.open(path) as pdf:
        for page in pdf.pages:
            for table in page.extract_tables() or []:
                parsed = _table_to_records(table)
                if not parsed:
                    continue
                page_headers, page_records = parsed
                if not headers:
                    headers = page_headers
                records.extend(page_records)
    if not records:
        raise ValueError("No holdings table could be read from the PDF")
    return headers, records


def _table_to_records(table: list[list[Any]]) -> tuple[list[str], list[dict[str, Any]]] | None:
    alias_pool = {alias for aliases in FIELD_ALIASES.values() for alias in aliases}
    header_idx = None
    best = 0
    for idx, row in enumerate(table[:10]):
        values = [clean_header(cell) for cell in row]
        hits = sum(1 for value in values if value in alias_pool)
        if hits > best:
            best = hits
            header_idx = idx
    if header_idx is None or best < 3:
        return None
    headers = []
    seen: dict[str, int] = {}
    for i, cell in enumerate(table[header_idx]):
        name = clean_header(cell) or f"col_{i}"
        count = seen.get(name, 0)
        seen[name] = count + 1
        headers.append(name if count == 0 else f"{name}_{count}")
    records: list[dict[str, Any]] = []
    for row in table[header_idx + 1 :]:
        if not any(cell for cell in row):
            continue
        item = {headers[i]: (row[i] if i < len(row) else None) for i in range(len(headers))}
        records.append(item)
    return headers, records
