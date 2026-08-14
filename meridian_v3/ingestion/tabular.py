from __future__ import annotations

from pathlib import Path
from typing import Any

import pandas as pd

from meridian_v3.ingestion.columns import FIELD_ALIASES, clean_header


def read_tabular(path: Path, sheet: str | int | None = None) -> tuple[list[str], list[dict[str, Any]]]:
    suffix = path.suffix.lower()
    if suffix in {".csv", ".txt"}:
        frame = _read_csv(path)
    elif suffix in {".xlsx", ".xls", ".xlsm"}:
        frame = _read_excel(path, sheet)
    else:
        raise ValueError(f"Unsupported tabular format: {suffix}")
    frame = _promote_header(frame)
    frame.columns = [clean_header(col) for col in frame.columns]
    frame = frame.dropna(how="all")
    records = frame.where(pd.notna(frame), None).to_dict(orient="records")
    return list(frame.columns), records


def _read_csv(path: Path) -> pd.DataFrame:
    for encoding in ("utf-8-sig", "utf-8", "cp1252", "latin-1"):
        try:
            return pd.read_csv(path, dtype=str, encoding=encoding, keep_default_na=False)
        except UnicodeDecodeError:
            continue
    return pd.read_csv(path, dtype=str, encoding="utf-8", errors="replace", keep_default_na=False)


def _read_excel(path: Path, sheet: str | int | None) -> pd.DataFrame:
    return pd.read_excel(path, sheet_name=sheet if sheet is not None else 0, dtype=str)


def _promote_header(frame: pd.DataFrame) -> pd.DataFrame:
    alias_pool = {alias for aliases in FIELD_ALIASES.values() for alias in aliases}
    best_idx = 0
    best_hits = 0
    current_hits = sum(1 for col in frame.columns if clean_header(col) in alias_pool)
    if current_hits >= 3:
        return frame
    scan = min(len(frame), 12)
    for idx in range(scan):
        values = [clean_header(v) for v in frame.iloc[idx].tolist()]
        hits = sum(1 for value in values if value in alias_pool)
        if hits > best_hits:
            best_hits = hits
            best_idx = idx
    if best_hits >= 3:
        new_header = [clean_header(v) or f"col_{i}" for i, v in enumerate(frame.iloc[best_idx].tolist())]
        body = frame.iloc[best_idx + 1 :].copy()
        body.columns = new_header
        return body
    return frame
