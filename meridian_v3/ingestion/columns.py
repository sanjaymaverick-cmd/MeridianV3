from __future__ import annotations

import re
from typing import Iterable

FIELD_ALIASES: dict[str, tuple[str, ...]] = {
    "symbol": (
        "symbol", "tradingsymbol", "trading symbol", "trade symbol", "instrument",
        "scrip", "scrip name", "scripcode", "ticker", "nse symbol", "nse code",
        "stock symbol", "security", "isin code", "scheme code",
    ),
    "company_name": (
        "company name", "company", "name", "stock name", "stock", "security name",
        "instrument name", "company_name", "scheme name", "scheme", "fund name",
    ),
    "quantity": (
        "qty", "qty.", "quantity", "qty available", "net qty", "net quantity",
        "free qty", "holdings qty", "shares", "no of shares", "no. of shares",
        "closing qty", "units", "unit balance", "folio units",
    ),
    "avg_cost": (
        "avg. cost", "avg cost", "avg cost price", "average cost", "average price",
        "avg price", "avg. price", "avg buy price", "buy avg", "buy average",
        "average buy price", "average cost price", "cost price", "wac",
        "avg nav", "average nav", "purchase nav",
    ),
    "last_price": (
        "ltp", "last traded price", "last price", "current price", "cmp",
        "market price", "current market price", "close price", "closing price",
        "prev close", "nav", "current nav",
    ),
    "invested": (
        "invested", "invested value", "investment", "invested amount",
        "total cost", "buy value", "cost value",
    ),
    "market_value": (
        "cur. val", "cur val", "current value", "market value", "present value",
        "value", "mkt value", "current amount",
    ),
    "isin": ("isin", "isin code"),
    "sector": ("sector", "industry", "industry name", "category"),
    "exchange": ("exchange", "exch", "segment"),
}

BROKER_SIGNATURES: dict[str, tuple[set[str], ...]] = {
    "zerodha": (
        {"instrument", "qty.", "avg. cost", "ltp"},
        {"tradingsymbol", "quantity", "average_price", "last_price"},
        {"instrument", "qty", "avg cost", "cur. val"},
    ),
    "angel_one": (
        {"trade symbol", "qty", "avg price", "ltp"},
        {"tradingsymbol", "quantity", "averageprice", "ltp"},
    ),
    "dhan": ({"trading symbol", "qty", "avg cost", "ltp"},),
    "icici": ({"stock", "quantity", "average cost price", "current market price"},),
    "groww": (
        {"stock name", "shares", "avg buy price", "current price"},
        {"scheme name", "units", "avg nav"},
    ),
    "upstox": ({"company", "quantity", "average price", "ltp"},),
    "mfcentral": ({"scheme name", "units", "isin"}, {"folio", "scheme", "closing units"}),
    "camsonline": ({"folio number", "scheme name", "unit balance"},),
    "kfintech": ({"folio", "scheme", "closing unit balance"},),
}


def clean_header(value: object) -> str:
    text = str(value or "").replace("\n", " ").replace("\u00a0", " ")
    text = re.sub(r"\s+", " ", text).strip().lower()
    return text


def infer_mapping(headers: Iterable[object]) -> dict[str, str]:
    cleaned = [clean_header(h) for h in headers]
    mapping: dict[str, str] = {}
    used: set[str] = set()
    for field, aliases in FIELD_ALIASES.items():
        for header in cleaned:
            if header in used:
                continue
            if header in aliases or header.replace("_", " ") in aliases:
                mapping[field] = header
                used.add(header)
                break
    return mapping


def detect_broker(headers: Iterable[object], filename: str = "") -> str:
    cleaned = {clean_header(h) for h in headers}
    file_hint = filename.lower()
    for broker, signatures in BROKER_SIGNATURES.items():
        if broker.replace("_", "") in file_hint.replace("_", "").replace("-", ""):
            return broker
        for signature in signatures:
            if signature.issubset(cleaned):
                return broker
    name_map = {
        "kite": "zerodha", "zerodha": "zerodha", "angel": "angel_one",
        "dhan": "dhan", "icici": "icici", "groww": "groww", "upstox": "upstox",
        "mfcentral": "mfcentral", "cams": "camsonline", "kfin": "kfintech",
    }
    for needle, broker in name_map.items():
        if needle in file_hint:
            return broker
    return "generic"
