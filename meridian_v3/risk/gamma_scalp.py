"""Gamma scalping mechanics.

Textbook discrete hedge-and-rehedge loop. Math stays here. The UI only
sees finished numbers and plain sentences.

Daily PnL is one-day theta (time). Gamma Scalping PnL is the ½ Γ (ΔS)²
term for a chosen move. Long gamma can add extra profit if you keep
delta small. Short gamma does the opposite and can add extra loss.

This module never sends an order.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from meridian_v3.risk.greeks_book import GreekSnapshot
from meridian_v3.risk.vega_engine import snap_lots

CHOICES = [
    "Accept and write an intended-trade note",
    "Dismiss this review",
    "Snooze and look again later",
    "Do nothing and keep watching",
]


@dataclass(frozen=True)
class ScalpStep:
    label: str
    price: float
    delta_lots: float
    hedge_lots: float
    locked_pnl: float
    note: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "label": self.label,
            "price": self.price,
            "delta_lots": self.delta_lots,
            "hedge_lots": self.hedge_lots,
            "locked_pnl": self.locked_pnl,
            "note": self.note,
        }


@dataclass(frozen=True)
class GammaScalpReport:
    posture: str
    helps: bool
    hurts: bool
    rehedge_band_lots: float
    current_delta_lots: float
    needs_rehedge: bool
    suggested_futures_lots: float
    daily_theta: float
    move_pct: float
    move_inr: float
    scalp_pnl_on_move: float
    net_after_move_day: float
    move_covers_theta: bool
    steps: list[ScalpStep] = field(default_factory=list)
    title: str = "Gamma scalping status (not an order)"
    status_lines: list[str] = field(default_factory=list)
    daily_pnl_line: str = ""
    gamma_scalp_line: str = ""
    suggestion: str = ""
    choices: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "posture": self.posture,
            "helps": self.helps,
            "hurts": self.hurts,
            "rehedge_band_lots": self.rehedge_band_lots,
            "current_delta_lots": self.current_delta_lots,
            "needs_rehedge": self.needs_rehedge,
            "suggested_futures_lots": self.suggested_futures_lots,
            "daily_theta": self.daily_theta,
            "move_pct": self.move_pct,
            "move_inr": self.move_inr,
            "scalp_pnl_on_move": self.scalp_pnl_on_move,
            "net_after_move_day": self.net_after_move_day,
            "move_covers_theta": self.move_covers_theta,
            "steps": [step.as_dict() for step in self.steps],
            "title": self.title,
            "status": self.status_lines,
            "daily_pnl": self.daily_pnl_line,
            "gamma_scalp_pnl": self.gamma_scalp_line,
            "suggestion": self.suggestion,
            "choices": self.choices,
        }


def _rupees(value: float) -> str:
    sign = "+" if value > 0 else ""
    return f"{sign}₹{value:,.0f}"


def _delta_after_move(delta_lots: float, gamma: float, move_inr: float) -> float:
    return delta_lots + gamma * move_inr


def explain_scalp(
    snap: GreekSnapshot,
    *,
    rehedge_band_lots: float = 1.0,
    lot_step: float = 1.0,
    start_hedged: bool = True,
) -> GammaScalpReport:
    start_delta = 0.0 if start_hedged else snap.delta_lots
    up = abs(snap.move_inr)
    down = -abs(snap.move_inr)
    scalp = snap.gamma_scalp_pnl
    net = snap.daily_pnl + scalp
    covers = abs(scalp) + 1e-9 >= abs(snap.daily_pnl) and snap.long_gamma
    futures_now = snap_lots(-snap.delta_lots, lot_step)
    needs = abs(snap.delta_lots) >= rehedge_band_lots - 1e-12

    if snap.long_gamma:
        posture_words = "Long gamma — a move can help if leftover direction stays small"
        up_note = (
            "Price went up. You now have extra long direction, so the model "
            "reviews selling futures at the higher price. That is the helpful loop."
        )
        down_note = (
            "Price went down. You now have extra short direction, so the model "
            "reviews buying futures at the lower price. That is the same helpful loop."
        )
        if needs:
            suggestion = (
                f"Model suggestion: review a futures clip of {futures_now:+.1f} lots "
                "to flatten leftover direction (review only). Gamma scalping is helping."
            )
        else:
            suggestion = (
                "Model suggestion: no futures clip yet — leftover direction is still "
                "inside the band. Keep watching. Gamma scalping is helping when a move comes."
            )
    elif snap.short_gamma:
        posture_words = "Short gamma — a move can hurt"
        up_note = (
            "Price went up. Short gamma pushed you the wrong way, so the model "
            "reviews buying futures at the higher price. That loop costs money."
        )
        down_note = (
            "Price went down. Short gamma pushed you the wrong way, so the model "
            "reviews selling futures at the lower price. That loop costs money."
        )
        suggestion = (
            "Model suggestion: this is not a harvest. Review cutting the short-gamma "
            f"option, or accept that a {snap.move_pct * 100:.1f}% jump can cost "
            f"about {_rupees(scalp)}. Not an order."
        )
    else:
        posture_words = "Gamma is flat — there is no scalp story"
        up_note = "A small up-move barely changes leftover direction."
        down_note = "A small down-move barely changes leftover direction."
        suggestion = "Model suggestion: nothing to scalp. Review only if leftover delta is large."

    delta_up = _delta_after_move(start_delta, snap.gamma, up)
    delta_down = _delta_after_move(start_delta, snap.gamma, down)
    steps = [
        ScalpStep(
            "Start (delta flattened)",
            snap.mark_inr,
            start_delta,
            snap_lots(-start_delta, lot_step),
            0.0,
            "Begin with leftover direction near zero so the next move is a clean gamma story.",
        ),
        ScalpStep(
            f"If price rises {snap.move_pct * 100:.1f}%",
            snap.mark_inr + up,
            delta_up,
            snap_lots(-delta_up, lot_step),
            scalp,
            up_note,
        ),
        ScalpStep(
            f"If price falls {snap.move_pct * 100:.1f}%",
            snap.mark_inr + down,
            delta_down,
            snap_lots(-delta_down, lot_step),
            scalp,
            down_note,
        ),
    ]

    if snap.daily_pnl < -1:
        daily_line = (
            f"Daily PnL: about {_rupees(snap.daily_pnl)} today if the market sits still "
            "(this is mostly time decay)."
        )
    elif snap.daily_pnl > 1:
        daily_line = (
            f"Daily PnL: about {_rupees(snap.daily_pnl)} today if the market sits still "
            "(time is paying you)."
        )
    else:
        daily_line = "Daily PnL: near zero if the market sits still."

    move = snap.move_pct * 100
    if snap.long_gamma:
        scalp_line = (
            f"Gamma Scalping PnL: a {move:.1f}% move could add about {_rupees(scalp)} "
            "if you keep delta small (gamma scalping is helping)."
        )
    elif snap.short_gamma:
        scalp_line = (
            f"Gamma Scalping PnL: a {move:.1f}% move could cost about {_rupees(scalp)} "
            "(short gamma — this is the opposite of a scalp, and it hurts)."
        )
    else:
        scalp_line = "Gamma Scalping PnL: near zero on a small move (gamma is flat)."

    band_words = (
        f"above the {rehedge_band_lots:.1f}-lot band — review a flatten"
        if needs
        else "still inside the band"
    )
    status = [
        f"Delta: leftover direction is {snap.delta_lots:+.2f} lots ({band_words}).",
        f"Gamma: {posture_words} ({snap.gamma:+.4f}).",
        f"After a {move:.1f}% move, leftover delta would be about {delta_up:+.2f} lots up "
        f"or {delta_down:+.2f} lots down.",
        f"Time: {_rupees(snap.theta)} today if nothing else happens.",
    ]

    return GammaScalpReport(
        posture=snap.gamma_sign,
        helps=snap.long_gamma,
        hurts=snap.short_gamma,
        rehedge_band_lots=rehedge_band_lots,
        current_delta_lots=snap.delta_lots,
        needs_rehedge=needs,
        suggested_futures_lots=futures_now if needs else 0.0,
        daily_theta=snap.daily_pnl,
        move_pct=snap.move_pct,
        move_inr=snap.move_inr,
        scalp_pnl_on_move=scalp,
        net_after_move_day=net,
        move_covers_theta=covers,
        steps=steps,
        status_lines=status,
        daily_pnl_line=daily_line,
        gamma_scalp_line=scalp_line,
        suggestion=suggestion,
        choices=list(CHOICES),
    )
