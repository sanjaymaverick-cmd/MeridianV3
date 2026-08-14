from __future__ import annotations

from decimal import Decimal
from typing import Any

from meridian_v3.domain.models import MappedRow
from meridian_v3.domain.money import to_decimal
from meridian_v3.domain.symbols import classify_instrument, normalize_isin, normalize_symbol
from meridian_v3.ingestion.columns import clean_header


def map_rows(
    records: list[dict[str, Any]],
    mapping: dict[str, str],
    default_exchange: str = "NSE",
) -> list[MappedRow]:
    rows: list[MappedRow] = []
    for record in records:
        cleaned = {clean_header(k): v for k, v in record.items()}
        raw = {k: "" if v is None else str(v) for k, v in cleaned.items()}
        try:
            symbol_raw = _pick(cleaned, mapping, "symbol") or _pick(cleaned, mapping, "company_name")
            if symbol_raw is None:
                raise ValueError("Missing symbol")
            parsed = normalize_symbol(symbol_raw, default_exchange)
            if mapping.get("exchange"):
                exch = str(_pick(cleaned, mapping, "exchange") or default_exchange)
                if "BSE" in exch.upper() or exch.upper() in {"BSE", "B", "BO"}:
                    parsed = normalize_symbol(parsed.symbol, "BSE")
            quantity = to_decimal(_pick(cleaned, mapping, "quantity"))
            avg_cost = to_decimal(_pick(cleaned, mapping, "avg_cost"))
            last_price = to_decimal(_pick(cleaned, mapping, "last_price"))
            invested = to_decimal(_pick(cleaned, mapping, "invested"))
            market_value = to_decimal(_pick(cleaned, mapping, "market_value"))
            if quantity is None or quantity <= 0:
                raise ValueError("Quantity missing or zero")
            if avg_cost is None or avg_cost <= 0:
                if invested is not None:
                    avg_cost = invested / quantity
                else:
                    raise ValueError("Average cost missing")
            if last_price is None and market_value is not None:
                last_price = market_value / quantity
            company = _pick(cleaned, mapping, "company_name")
            sector = _pick(cleaned, mapping, "sector")
            isin = normalize_isin(_pick(cleaned, mapping, "isin"))
            name = str(company) if company else parsed.symbol
            rows.append(
                MappedRow(
                    symbol=parsed.symbol,
                    exchange=parsed.exchange,
                    company_name=name,
                    isin=isin,
                    quantity=quantity,
                    avg_cost=avg_cost,
                    last_price=last_price,
                    sector=str(sector) if sector else None,
                    instrument=classify_instrument(name, parsed.symbol, isin),
                    raw=raw,
                )
            )
        except Exception as exc:  # noqa: BLE001
            rows.append(
                MappedRow(
                    symbol="INVALID",
                    exchange=default_exchange,
                    quantity=Decimal("0"),
                    avg_cost=Decimal("0"),
                    raw=raw,
                    error=str(exc),
                )
            )
    return rows


def _pick(record: dict[str, Any], mapping: dict[str, str], field: str) -> Any:
    key = mapping.get(field)
    if not key:
        return None
    return record.get(key)
