"""Drawdown-aware risk reduction and the 20% live-pause rule.

    dd          = 1 - equity / peak
    scale       = 1                         if dd < start
                = 1 - (dd - start) / span   if start ≤ dd < pause
                = 0                         if dd ≥ pause

When scale hits 0, new live trades pause. Open positions stay open.
Paper trades keep running so we can still learn.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class DrawdownState:
    equity: float
    peak: float
    drawdown: float
    scale: float
    live_paused: bool
    reason: str


def update_peak(peak: float, equity: float) -> float:
    return max(peak, equity)


def drawdown_fraction(equity: float, peak: float) -> float:
    if peak <= 0:
        return 0.0
    return max(0.0, 1.0 - equity / peak)


def risk_scale(
    drawdown: float,
    *,
    start: float = 0.08,
    pause: float = 0.20,
) -> float:
    if drawdown < start:
        return 1.0
    if drawdown + 1e-12 >= pause:
        return 0.0
    span = max(pause - start, 1e-9)
    return max(0.0, 1.0 - (drawdown - start) / span)


def assess_drawdown(
    equity: float,
    peak: float,
    *,
    start: float = 0.08,
    pause: float = 0.20,
) -> DrawdownState:
    peak = update_peak(peak, equity)
    dd = drawdown_fraction(equity, peak)
    scale = risk_scale(dd, start=start, pause=pause)
    paused = dd >= pause - 1e-12
    if paused:
        reason = (
            f"Live trading is paused. The book is down {dd:.0%} from its peak "
            f"of ₹{peak:,.0f}. Open positions may stay. New live trades wait."
        )
    elif scale < 1:
        reason = (
            f"Risk is reduced. The book is down {dd:.0%} from peak, "
            f"so new size is {scale:.0%} of normal."
        )
    else:
        reason = "Drawdown is small. Full size is allowed."
    return DrawdownState(
        equity=equity,
        peak=peak,
        drawdown=dd,
        scale=scale,
        live_paused=paused,
        reason=reason,
    )
