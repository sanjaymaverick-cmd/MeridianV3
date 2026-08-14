"""Volatility-normalized (ATR) position sizing.

Risk a fixed rupee amount, then convert that into shares using ATR:

    stop   = ATR(n) × k
    qty    = floor(risk_rupees / stop)

A jumpy name gets fewer shares. A quiet name can take more, still
capped by the cash on the dedicated account.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence


@dataclass(frozen=True)
class AtrResult:
    atr: float
    stop: float
    qty: float
    notional: float
    risk_rupees: float


def true_range(high: float, low: float, prev_close: float) -> float:
    return max(high - low, abs(high - prev_close), abs(low - prev_close))


def average_true_range(
    highs: Sequence[float],
    lows: Sequence[float],
    closes: Sequence[float],
    period: int = 14,
) -> float:
    if len(closes) < 2:
        return 0.0
    n = min(len(closes), len(highs), len(lows))
    ranges: list[float] = []
    for i in range(1, n):
        ranges.append(true_range(highs[i], lows[i], closes[i - 1]))
    if not ranges:
        return 0.0
    use = ranges[-period:]
    return sum(use) / len(use)


def atr_quantity(
    *,
    risk_rupees: float,
    atr: float,
    price: float,
    stop_mult: float = 1.5,
    lot_step: float = 1.0,
    cash: float | None = None,
    max_notional: float | None = None,
) -> AtrResult:
    stop = max(atr * stop_mult, price * 0.004, 0.01)
    raw = risk_rupees / stop if stop > 0 else 0.0
    if lot_step > 0:
        qty = (raw // lot_step) * lot_step
    else:
        qty = raw
    notional = qty * price
    if cash is not None and notional > cash and price > 0:
        qty = (cash / price // max(lot_step, 1e-9)) * lot_step
        notional = qty * price
    if max_notional is not None and notional > max_notional and price > 0:
        qty = (max_notional / price // max(lot_step, 1e-9)) * lot_step
        notional = qty * price
    if qty < 0:
        qty = 0.0
        notional = 0.0
    return AtrResult(
        atr=atr,
        stop=stop,
        qty=qty,
        notional=notional,
        risk_rupees=qty * stop,
    )
