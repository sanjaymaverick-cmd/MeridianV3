from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from meridian_v3.signals.chart_payload import (
    ChartLevel,
    ChartMarker,
    ChartWindow,
    ChartZone,
    build_chart_payload,
)
from meridian_v3.storage.schema import PriceBar, PriceCache, SignalRow


def chart_for(session: Session, symbol: str) -> dict:
    symbol = symbol.upper()
    bars = list(
        session.scalars(
            select(PriceBar).where(PriceBar.symbol == symbol).order_by(PriceBar.bar_date.asc())
        )
    )
    cache = session.scalar(select(PriceCache).where(PriceCache.symbol == symbol))
    signals = list(
        session.scalars(
            select(SignalRow).where(SignalRow.symbol == symbol).order_by(SignalRow.created_at.desc()).limit(8)
        )
    )
    markers: list[ChartMarker] = []
    for sig in signals:
        if not bars:
            break
        side = "long" if sig.side == "buy" else "short" if sig.side == "sell" else "long"
        markers.append(
            ChartMarker(
                time=bars[-1].bar_date.isoformat(),
                side=side,
                text="Look at a long / entry" if side == "long" else "Look at a short / exit",
                confidence=int(round(sig.confidence * 100)),
            )
        )
    levels = []
    zones = []
    windows = []
    if cache:
        if cache.sma20:
            levels.append(ChartLevel(cache.sma20, "Slow average"))
        if cache.sma50:
            levels.append(ChartLevel(cache.sma50, "Longer average", "#8A7340"))
        if cache.low20 and cache.high20:
            zones.append(ChartZone(cache.low20, cache.high20, "Recent range"))
    if len(bars) >= 8:
        windows.append(
            ChartWindow(
                bars[-8].bar_date.isoformat(),
                bars[-1].bar_date.isoformat(),
                "Higher-attention window",
            )
        )
    payload = build_chart_payload(
        symbol=symbol,
        label=f"{symbol} · CASH" if symbol != "USDINR" else f"{symbol} · FX",
        quality=cache.quality if cache else "missing",
        bars=bars,
        markers=markers,
        levels=levels,
        zones=zones,
        windows=windows,
    )
    return payload.as_dict()
