"""Pre-computed chart DTO. Lightweight Charts never sees raw models."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, Iterable, Sequence


@dataclass(frozen=True)
class Candle:
    time: str
    open: float
    high: float
    low: float
    close: float
    volume: float = 0.0


@dataclass(frozen=True)
class ChartMarker:
    time: str
    side: str
    text: str
    confidence: int | None = None

    def as_dict(self) -> dict[str, Any]:
        long = self.side == "long"
        label = self.text
        if self.confidence is not None:
            label = f"{self.text} · {self.confidence}"
        return {
            "time": self.time,
            "position": "belowBar" if long else "aboveBar",
            "shape": "diamond",
            "color": "#4A9B7F" if long else "#C46B6B",
            "text": label,
        }


@dataclass(frozen=True)
class ChartLevel:
    price: float
    title: str
    color: str = "#C4A35A"


@dataclass(frozen=True)
class ChartZone:
    low: float
    high: float
    title: str
    color: str = "rgba(196,163,90,0.12)"


@dataclass(frozen=True)
class ChartWindow:
    start: str
    end: str
    title: str
    color: str = "rgba(74,155,127,0.10)"


@dataclass
class ChartPayload:
    symbol: str
    label: str
    quality: str
    candles: list[Candle] = field(default_factory=list)
    markers: list[ChartMarker] = field(default_factory=list)
    levels: list[ChartLevel] = field(default_factory=list)
    zones: list[ChartZone] = field(default_factory=list)
    windows: list[ChartWindow] = field(default_factory=list)
    signal: list[dict[str, Any]] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "label": self.label,
            "quality": self.quality,
            "candles": [c.__dict__ for c in self.candles],
            "markers": [m.as_dict() for m in self.markers],
            "levels": [lv.__dict__ for lv in self.levels],
            "zones": [z.__dict__ for z in self.zones],
            "windows": [w.__dict__ for w in self.windows],
            "signal": self.signal,
            "legend": {
                "long": "Green diamond = look at a long / entry",
                "short": "Red diamond = look at a short / exit",
                "level": "Gold line = a key level the desk already computed",
                "zone": "Soft band = a recent range, not a target",
                "window": "Shaded days = a higher-attention window",
            },
            "poll_seconds": 30,
        }


def _iso(value: date | datetime | str) -> str:
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    return str(value)[:10]


def candles_from_bars(bars: Sequence[Any]) -> list[Candle]:
    out: list[Candle] = []
    for bar in bars:
        day = getattr(bar, "bar_date", None) or getattr(bar, "as_of", None) or getattr(bar, "time", None)
        if day is None:
            continue
        out.append(
            Candle(
                time=_iso(day),
                open=float(bar.open),
                high=float(bar.high),
                low=float(bar.low),
                close=float(bar.close),
                volume=float(getattr(bar, "volume", 0) or 0),
            )
        )
    return out


def smooth_closes(candles: Sequence[Candle], window: int = 5) -> list[dict[str, Any]]:
    if len(candles) < 2:
        return []
    values = [c.close for c in candles]
    line: list[dict[str, Any]] = []
    run = 0.0
    for index, candle in enumerate(candles):
        run += values[index]
        if index >= window:
            run -= values[index - window]
        if index >= window - 1:
            line.append({"time": candle.time, "value": run / window})
    return line


def build_chart_payload(
    *,
    symbol: str,
    label: str,
    quality: str,
    bars: Sequence[Any],
    markers: Iterable[ChartMarker] = (),
    levels: Iterable[ChartLevel] = (),
    zones: Iterable[ChartZone] = (),
    windows: Iterable[ChartWindow] = (),
    include_signal: bool = True,
) -> ChartPayload:
    candles = candles_from_bars(bars)
    return ChartPayload(
        symbol=symbol,
        label=label,
        quality=quality,
        candles=candles,
        markers=list(markers),
        levels=list(levels),
        zones=list(zones),
        windows=list(windows),
        signal=smooth_closes(candles) if include_signal else [],
    )
