from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from shutil import copy2

from meridian_v3.config import get_settings
from meridian_v3.domain.models import ImportPreview, MappedRow
from meridian_v3.ingestion.columns import detect_broker, infer_mapping
from meridian_v3.ingestion.normalize import map_rows
from meridian_v3.ingestion.ocr import read_image
from meridian_v3.ingestion.pdf import read_pdf_tables
from meridian_v3.ingestion.tabular import read_tabular
from meridian_v3.portfolio.mapper import map_into_book
from meridian_v3.storage.schema import Holding, ImportJob


IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".tif", ".tiff", ".bmp"}


def parse_statement(path: Path) -> tuple[list[str], list[dict], str, str]:
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        headers, records = read_pdf_tables(path)
        return headers, records, "pdf", ""
    if suffix in IMAGE_SUFFIXES:
        headers, records, note = read_image(path)
        return headers, records, "image", note
    headers, records = read_tabular(path)
    return headers, records, "tabular", ""


class ImportService:
    def __init__(self, session) -> None:  # noqa: ANN001
        self.session = session

    def preview(
        self,
        path: Path,
        *,
        mapping: dict[str, str] | None = None,
        broker: str | None = None,
    ) -> ImportPreview:
        headers, records, kind, note = parse_statement(path)
        resolved_broker = broker or detect_broker(headers, path.name)
        resolved_mapping = mapping or infer_mapping(headers)
        rows = map_rows(records, resolved_mapping)
        accepted = sum(1 for row in rows if not row.error)
        rejected = len(rows) - accepted
        return ImportPreview(
            broker=resolved_broker,
            filename=path.name,
            headers=headers,
            mapping=resolved_mapping,
            rows=rows,
            accepted=accepted,
            rejected=rejected,
            source_kind=kind,
            notes=note,
        )

    def commit(
        self,
        preview: ImportPreview,
        *,
        account_name: str,
        confirmed: list[MappedRow] | None = None,
        source_path: Path | None = None,
    ) -> tuple[int, int]:
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        rows = confirmed if confirmed is not None else preview.rows
        accepted = 0
        rejected = 0
        for row in rows:
            if row.error:
                rejected += 1
                continue
            holding = Holding(
                symbol=row.symbol,
                exchange=row.exchange,
                company_name=row.company_name or row.symbol,
                isin=row.isin,
                instrument=row.instrument,
                quantity=float(row.quantity),
                avg_cost=float(row.avg_cost),
                last_price=float(row.last_price) if row.last_price is not None else None,
                source="import",
                account_name=account_name,
                created_at=now,
            )
            self.session.add(holding)
            map_into_book(self.session, holding)
            accepted += 1
        archived = ""
        if source_path and source_path.exists():
            dest = get_settings().import_dir
            dest.mkdir(parents=True, exist_ok=True)
            target = dest / source_path.name
            try:
                copy2(source_path, target)
                archived = str(target)
            except OSError:
                archived = ""
        self.session.add(
            ImportJob(
                filename=preview.filename,
                broker=preview.broker,
                source_kind=preview.source_kind,
                status="committed",
                accepted=accepted,
                rejected=rejected,
                notes=preview.notes or archived,
                created_at=now,
            )
        )
        self.session.flush()
        return accepted, rejected
