"""Capital & Position Sizing Engine.

Combines:
  1. confidence-weighted fractional Kelly
  2. ATR volatility normalization
  3. drawdown scale
  4. ₹5,000 cash / lot / market constraints
  5. compounding (the whole book is the bankroll)
"""

from __future__ import annotations

from dataclasses import dataclass

from meridian_v3.config import Settings
from meridian_v3.engine.atr import atr_quantity
from meridian_v3.engine.drawdown import DrawdownState
from meridian_v3.engine.kelly import confidence_weighted_fractional_kelly


@dataclass(frozen=True)
class SizePlan:
    qty: float
    notional: float
    risk_rupees: float
    risk_pct: float
    kelly_frac: float
    stop: float
    market: str
    horizon: str
    reason: str
    blocked: bool


def risk_cap_pct(confidence: float, settings: Settings) -> float:
    if confidence >= settings.sizing.high_confidence:
        return settings.sizing.max_risk_pct_high_conf
    if confidence >= 0.55:
        return settings.sizing.max_risk_pct_normal
    return settings.sizing.max_risk_pct_low


def size_position(
    *,
    equity: float,
    cash: float,
    price: float,
    atr: float,
    p_success: float,
    payoff: float,
    confidence: float,
    drawdown: DrawdownState,
    settings: Settings,
    market: str = "equity_cash",
    lot_step: float = 1.0,
    open_count: int = 0,
) -> SizePlan:
    if price <= 0 or equity <= 0:
        return SizePlan(0, 0, 0, 0, 0, 0, market, "intraday", "No price or no book.", True)

    kelly = confidence_weighted_fractional_kelly(
        p=p_success,
        b=max(payoff, 0.25),
        confidence=confidence,
        kappa=settings.sizing.kelly_fraction,
        fmin=0.0,
        fmax=settings.sizing.max_kelly_fraction,
    )
    cap = risk_cap_pct(confidence, settings)
    risk_pct = min(kelly.sized, cap) * drawdown.scale
    reserve = equity * settings.sizing.cash_reserve_pct
    spendable = max(0.0, cash - reserve)
    risk_rupees = max(settings.sizing.min_risk_inr, equity * risk_pct)
    max_notional = min(spendable, equity * settings.sizing.max_position_pct)

    high = confidence >= settings.sizing.high_confidence
    max_open = settings.sizing.max_concurrent_high if high else settings.sizing.max_concurrent_normal
    if open_count >= max_open:
        return SizePlan(
            0, 0, 0, risk_pct, kelly.sized, 0, market, "intraday",
            f"Already {open_count} open clips. Wait — the small book cannot wear more.",
            True,
        )

    if market == "options_buy":
        prem_cap = equity * settings.markets.options_buy.max_premium_pct_of_equity
        qty = 1.0 if price <= min(max_notional, prem_cap) else 0.0
        if qty <= 0:
            return SizePlan(
                0, 0, 0, risk_pct, kelly.sized, price, market, "intraday",
                "Option premium does not fit the ₹5,000 book. Options buying only, and only a small premium.",
                True,
            )
        return SizePlan(
            qty, price, min(price, risk_rupees), risk_pct, kelly.sized, price,
            market, "intraday",
            f"One option lot. Premium ₹{price:,.0f}. Buying only — never selling premium on this book.",
            False,
        )

    if market == "forex_micro":
        lot = settings.markets.forex_micro.min_lot
        notional = lot * price
        if notional > max_notional:
            return SizePlan(
                0, 0, 0, risk_pct, kelly.sized, 0, market, "intraday",
                "Even a nano lot is too large for cash on hand.",
                True,
            )
        return SizePlan(
            lot, notional, risk_rupees, risk_pct, kelly.sized, atr * settings.sizing.atr_stop_mult,
            market, "intraday",
            f"Forex nano/micro only: {lot:g} lot. Standard lots are forbidden.",
            False,
        )

    atr_plan = atr_quantity(
        risk_rupees=risk_rupees,
        atr=atr,
        price=price,
        stop_mult=settings.sizing.atr_stop_mult,
        lot_step=lot_step,
        cash=spendable,
        max_notional=max_notional,
    )
    if atr_plan.qty <= 0:
        if price <= spendable and price <= max_notional:
            return SizePlan(
                1, price, min(risk_rupees, atr_plan.stop or price * 0.015),
                risk_pct, kelly.sized, atr_plan.stop or price * 0.015,
                market, "intraday",
                "ATR wanted zero shares. We still allow one share so the ₹5,000 book can work.",
                False,
            )
        return SizePlan(
            0, 0, 0, risk_pct, kelly.sized, atr_plan.stop, market, "intraday",
            "Cannot fit even one share after the cash reserve.",
            True,
        )

    horizon = "positional" if confidence >= settings.sizing.positional_confidence else "intraday"
    return SizePlan(
        qty=atr_plan.qty,
        notional=atr_plan.notional,
        risk_rupees=atr_plan.risk_rupees,
        risk_pct=risk_pct,
        kelly_frac=kelly.sized,
        stop=atr_plan.stop,
        market=market,
        horizon=horizon,
        reason=(
            f"{atr_plan.qty:g} shares. Risk about ₹{atr_plan.risk_rupees:,.0f} "
            f"({risk_pct:.1%} of the book). Stop near ₹{atr_plan.stop:,.2f}. {kelly.reason}"
        ),
        blocked=False,
    )
