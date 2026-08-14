from pathlib import Path

from meridian_v3.ingestion.columns import detect_broker, infer_mapping
from meridian_v3.ingestion.normalize import map_rows
from meridian_v3.ingestion.ocr import text_to_records
from meridian_v3.ingestion.tabular import read_tabular


def test_zerodha_csv(tmp_path: Path):
    path = tmp_path / "zerodha.csv"
    path.write_text(
        "Instrument,Qty.,Avg. cost,LTP\nRELIANCE,10,1200.5,1380\nHDFCBANK,5,1500,1660\n",
        encoding="utf-8",
    )
    headers, records = read_tabular(path)
    assert detect_broker(headers, path.name) == "zerodha"
    mapping = infer_mapping(headers)
    rows = map_rows(records, mapping)
    assert rows[0].error is None
    assert rows[0].symbol == "RELIANCE"
    assert float(rows[0].quantity) == 10


def test_ocr_line_parse():
    text = "RELIANCE 12 1200.50 1384.00\nINFY 8 1400 1488"
    headers, records = text_to_records(text)
    assert len(records) == 2
    assert records[0]["stock name"] == "RELIANCE"
