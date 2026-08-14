"""Net Greeks and the two PnL lines shown on every major review.

Math stays here. The UI only sees rounded numbers and plain sentences.
Daily PnL is one-day theta. Gamma-scalp PnL is the textbook ½ Γ (ΔS)²
term for a chosen move size — help if gamma is long, hurt if short.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Sequence

from meridian_v3.risk.vega_engine import OptionGreekLeg


@dataclass(frozen=True)
class GreekSnapshot:
    symbol: str
    as_of: date
    delta_lots: float
    gamma: float
    vega: float
    theta: float
    mark_inr: float
    multiplier: float
    move_pct: float
    move_inr: float
    daily_pnl: float
    gamma_scalp_pnl: float
    gamma_sign: str
    scalp_helps: bool
    stale: bool
    leg_count: int

    @property
    def long_gamma(self) -> bool:
        return self.gamma > 1e-12

    @property
    def short_gamma(self) -> bool:
        return self.gamma < -1e-12


def net_greeks(legs: Sequence[OptionGreekLeg]) -> tuple[float, float, float, float]:
    delta = sum(leg.effective_delta_lots for leg in legs)
    gamma = sum(leg.gamma_lots for leg in legs)
    vega = sum(leg.vega for leg in legs)
    theta = sum(leg.lots * leg.theta_per_lot for leg in legs)
    return delta, gamma, vega, theta


def gamma_scalp_pnl(*, gamma: float, move_inr: float, multiplier: float) -> float:
    """½ Γ (ΔS)² in rupees. Sign follows gamma: long helps, short hurts."""
    return 0.5 * gamma * (move_inr**2) * multiplier


def snapshot_from_legs(
    symbol: str,
    legs: Sequence[OptionGreekLeg],
    as_of: date,
    *,
    move_pct: float = 0.01,
) -> GreekSnapshot:
    use = [leg for leg in legs if leg.symbol == symbol]
    delta, gamma, vega, theta = net_greeks(use)
    mark = next((leg.mark_inr for leg in use if leg.mark_inr), 0.0)
    multiplier = next((leg.multiplier for leg in use if leg.multiplier), 1.0)
    move_inr = mark * move_pct
    scalp = gamma_scalp_pnl(gamma=gamma, move_inr=move_inr, multiplier=multiplier)
    if gamma > 1e-12:
        sign = "long"
    elif gamma < -1e-12:
        sign = "short"
    else:
        sign = "flat"
    return GreekSnapshot(
        symbol=symbol,
        as_of=as_of,
        delta_lots=delta,
        gamma=gamma,
        vega=vega,
        theta=theta,
        mark_inr=mark,
        multiplier=multiplier,
        move_pct=move_pct,
        move_inr=move_inr,
        daily_pnl=theta,
        gamma_scalp_pnl=scalp,
        gamma_sign=sign,
        scalp_helps=gamma > 1e-12,
        stale=any(leg.stale for leg in use),
        leg_count=len(use),
    )
