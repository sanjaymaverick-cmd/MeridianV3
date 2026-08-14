"""Six vega actions. Each one also reports the hit to Delta, Gamma, and Theta.

None of these send an order. They size a review the human can accept or ignore.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

from meridian_v3.domain.models import RegimeLabel
from meridian_v3.risk.greeks_book import GreekSnapshot
from meridian_v3.risk.vega_engine import VegaPolicy, snap_lots, utilization


@dataclass(frozen=True)
class HedgeUnit:
    vega: float
    delta: float
    gamma: float
    theta: float


@dataclass(frozen=True)
class VegaAction:
    key: str
    title: str
    enabled: bool
    opt_lots: float
    d_delta: float
    d_gamma: float
    d_vega: float
    d_theta: float
    after_vega: float
    after_util: float
    note: str

    def as_dict(self) -> dict:
        return {
            "key": self.key,
            "title": self.title,
            "enabled": self.enabled,
            "opt_lots": self.opt_lots,
            "effects": {
                "delta": self.d_delta,
                "gamma": self.d_gamma,
                "vega": self.d_vega,
                "theta": self.d_theta,
            },
            "after_vega": self.after_vega,
            "after_util": self.after_util,
            "note": self.note,
        }


REGIME_LIMIT_SCALE = {
    RegimeLabel.CALM.value: 1.20,
    RegimeLabel.ELEVATED.value: 1.00,
    RegimeLabel.STRESS.value: 0.70,
}


def _apply(opt_lots: float, unit: HedgeUnit) -> tuple[float, float, float, float]:
    return (
        opt_lots * unit.delta,
        opt_lots * unit.gamma,
        opt_lots * unit.vega,
        opt_lots * unit.theta,
    )


def _action(
    key: str,
    title: str,
    opt_lots: float,
    unit: HedgeUnit,
    snap: GreekSnapshot,
    limit: float,
    note: str,
    *,
    enabled: bool = True,
) -> VegaAction:
    d_delta, d_gamma, d_vega, d_theta = _apply(opt_lots, unit)
    after = snap.vega + d_vega
    return VegaAction(
        key=key,
        title=title,
        enabled=enabled and abs(opt_lots) > 1e-12,
        opt_lots=opt_lots,
        d_delta=d_delta,
        d_gamma=d_gamma,
        d_vega=d_vega,
        d_theta=d_theta,
        after_vega=after,
        after_util=utilization(after, limit) if limit else 0.0,
        note=note,
    )


def plan_vega_actions(
    snap: GreekSnapshot,
    policy: VegaPolicy,
    unit: HedgeUnit,
    *,
    regime: str = "Elevated",
    days_to_expiry: int | None = None,
    peer_vega: Sequence[tuple[str, float]] = (),
    lot_step: float | None = None,
) -> list[VegaAction]:
    step = policy.lot_step if lot_step is None else lot_step
    hvp = unit.vega
    util = utilization(snap.vega, policy.vega_limit)

    raw_limit = 0.0
    if abs(snap.vega) > policy.vega_limit + 1e-6 and abs(hvp) > 1e-12:
        raw_limit = (policy.nu_star - snap.vega) / hvp
    a1 = _action(
        "limit_cap",
        "Stay under the vega line",
        snap_lots(raw_limit, step),
        unit,
        snap,
        policy.vega_limit,
        f"Used {util:.0%} of the {policy.vega_limit:,.0f} ₹/vol-pt line.",
    )

    raw_cut = 0.0
    if abs(hvp) > 1e-12 and abs(snap.vega) > 1e-6:
        raw_cut = (0.0 - snap.vega) / hvp
    a2 = _action(
        "cut_options",
        "Shrink the option itself",
        snap_lots(raw_cut, step),
        unit,
        snap,
        policy.vega_limit,
        "Take lots off this option so vega moves toward zero.",
    )
    a3 = _action(
        "hedge_options",
        "Add an opposite option",
        snap_lots(raw_cut, step),
        unit,
        snap,
        policy.vega_limit,
        "Pair with a different strike or type so net vega comes down.",
    )

    peers = [(name, value) for name, value in peer_vega if name != snap.symbol]
    opposite = [item for item in peers if item[1] * snap.vega < 0]
    balance_note = "No other name with the other side of vega is on the book."
    raw_bal = 0.0
    if opposite and abs(hvp) > 1e-12:
        peer_name, peer_val = max(opposite, key=lambda item: abs(item[1]))
        target = snap.vega + peer_val
        raw_bal = (target / 2 - snap.vega) / hvp
        balance_note = (
            f"{peer_name} sits on the other side ({peer_val:,.0f} ₹/vol-pt). "
            "A smaller clip here would even the book."
        )
    a4 = _action(
        "book_balance",
        "Balance vega across the book",
        snap_lots(raw_bal, step),
        unit,
        snap,
        policy.vega_limit,
        balance_note,
        enabled=bool(opposite),
    )

    scale = REGIME_LIMIT_SCALE.get(regime, 1.0)
    regime_limit = policy.vega_limit * scale
    raw_reg = 0.0
    if abs(snap.vega) > regime_limit + 1e-6 and abs(hvp) > 1e-12:
        raw_reg = (policy.nu_star - snap.vega) / hvp
    a5 = _action(
        "regime_limit",
        "Tighten the line for this market mood",
        snap_lots(raw_reg, step),
        unit,
        snap,
        regime_limit,
        f"{regime} mood uses {scale:.0%} of the usual vega line ({regime_limit:,.0f} ₹/vol-pt).",
    )

    raw_time = 0.0
    time_note = "No expiry date on file."
    if days_to_expiry is not None:
        if days_to_expiry <= 0:
            time_note = "This option has expired. Vega should already be gone."
            if abs(hvp) > 1e-12:
                raw_time = (0.0 - snap.vega) / hvp
        elif days_to_expiry <= 21 and abs(hvp) > 1e-12:
            keep = days_to_expiry / 21.0
            raw_time = (snap.vega * keep - snap.vega) / hvp
            time_note = (
                f"{days_to_expiry} days left. Model keeps {keep:.0%} of today's vega "
                "and lets the rest roll off."
            )
        else:
            time_note = f"{days_to_expiry} days left. No extra time cut yet."
    a6 = _action(
        "time_reduce",
        "Let vega fall as expiry gets close",
        snap_lots(raw_time, step),
        unit,
        snap,
        policy.vega_limit,
        time_note,
    )
    return [a1, a2, a3, a4, a5, a6]
