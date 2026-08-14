"""Capital & Position Sizing Engine.

Combines:
  1. confidence-weighted fractional Kelly
  2. ATR volatility normalization
  3. drawdown scale
  4. ₹50,000 cash / lot / market constraints
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
    reserve = min(equity * settings.sizing.cash_reserve_pct, cash * 0.35)
    spendable = max(0.0, cash - reserve)
    risk_rupees = max(settings.sizing.min_risk_inr, equity * risk_pct)
    max_notional = min(spendable, equity * settings.sizing.max_position_pct)

    high = confidence >= settings.sizing.high_confidence
    max_open = settings.sizing.max_concurrent_high if high else settings.sizing.max_concurrent_normal
    if open_count >= max_open:
        return SizePlan(
            0, 0, 0, risk_pct, kelly.sized, 0, market, "intraday",
            f"Already {open_count} open clips. Wait — the book is full enough.",
            True,
        )

    if market == "options_buy":
        prem_cap = equity * settings.markets.options_buy.max_premium_pct_of_equity
        qty = 1.0 if price <= min(max_notional, prem_cap) else 0.0
        if qty <= 0:
            return SizePlan(
                0, 0, 0, risk_pct, kelly.sized, price, market, "intraday",
                "Option premium does not fit the ₹50,000 book. Options buying only, and only a small premium.",
                True,
            )
        return SizePlan(
            qty, price, min(price, risk_rupees), risk_pct, kelly.sized, price,
            market, "intraday",
            f"One option lot. Premium ₹{price:,.0f}. Buying only — never selling premium on this book.",
            False,
        )

    if market == "crypto_spot":
        return _crypto_size(
            equity=equity,
            spendable=spendable,
            price=price,
            atr=atr,
            risk_rupees=risk_rupees,
            risk_pct=risk_pct,
            kelly_frac=kelly.sized,
            settings=settings,
            market=market,
            margin_mult=1.0,
            word="spot coin",
        )

    if market == "crypto_futures":
        lev = max(1.0, settings.markets.crypto_futures.max_leverage)
        return _crypto_size(
            equity=equity,
            spendable=spendable,
            price=price,
            atr=atr,
            risk_rupees=risk_rupees,
            risk_pct=risk_pct,
            kelly_frac=kelly.sized,
            settings=settings,
            market=market,
            margin_mult=1.0 / lev,
            word=f"crypto future ({lev:.0f}x max)",
        )

    if market == "crypto_options":
        prem = max(price, 1.0)
        step = settings.markets.crypto_options.lot_step
        qty = max(step, (risk_rupees / prem // step) * step) if prem else 0.0
        notional = qty * prem
        if notional > min(max_notional, spendable):
            qty = (min(max_notional, spendable) / prem // step) * step
            notional = qty * prem
        if qty <= 0:
            return SizePlan(0, 0, 0, risk_pct, kelly.sized, prem, market, "intraday",
                            "Crypto option premium does not fit. Buying only.", True)
        return SizePlan(
            qty, notional, min(notional, risk_rupees), risk_pct, kelly.sized, prem,
            market, "intraday",
            f"{qty:g} crypto option unit(s). Premium about ₹{prem:,.0f} each. Buying only.",
            False,
        )

    if market == "india_futures":
        spec = settings.markets.india_futures
        step = spec.lot_step
        contract = spec.contract_size
        notional_1 = price * contract * step
        margin_1 = notional_1 * spec.margin_pct
        if margin_1 <= 0:
            return SizePlan(0, 0, 0, risk_pct, kelly.sized, 0, market, "intraday", "No futures mark.", True)
        raw_lots = max(step, (min(risk_rupees, spendable, max_notional) / margin_1 // 1) * step)
        lots = raw_lots
        while lots >= step and lots * margin_1 > min(spendable, max_notional) + 1e-9:
            lots = round(lots - step, 6)
        if lots < step:
            return SizePlan(
                0, 0, 0, risk_pct, kelly.sized, price * spec.margin_pct,
                market, "intraday",
                "Even a paper mini-lot of this future needs more cash than we have free.",
                True,
            )
        margin = lots * margin_1
        return SizePlan(
            lots, margin, min(risk_rupees, margin), risk_pct, kelly.sized,
            atr * settings.sizing.atr_stop_mult,
            market, "intraday",
            f"{lots:g} India mini-future lot(s). Margin about ₹{margin:,.0f} "
            f"(full exchange lots are too big for this book).",
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
                "ATR wanted zero shares. We still allow one share so the book can work.",
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


def _crypto_size(
    *,
    equity: float,
    spendable: float,
    price: float,
    atr: float,
    risk_rupees: float,
    risk_pct: float,
    kelly_frac: float,
    settings: Settings,
    market: str,
    margin_mult: float,
    word: str,
) -> SizePlan:
    spec = settings.markets.crypto_spot if market == "crypto_spot" else settings.markets.crypto_futures
    step = spec.lot_step
    stop = max(atr * settings.sizing.atr_stop_mult, price * 0.008, 0.01)
    raw = risk_rupees / stop if stop else 0.0
    qty = (raw // step) * step
    notional = qty * price
    margin = notional * margin_mult
    cap = min(spendable, equity * settings.sizing.max_position_pct)
    if margin > cap and price > 0 and margin_mult > 0:
        qty = (cap / (price * margin_mult) // step) * step
        notional = qty * price
        margin = notional * margin_mult
    if qty < spec.min_lot:
        return SizePlan(
            0, 0, 0, risk_pct, kelly_frac, stop, market, "intraday",
            f"Even a crumb of this {word} is too large for free cash.",
            True,
        )
    return SizePlan(
        qty, margin if margin_mult < 1 else notional, qty * stop, risk_pct, kelly_frac, stop,
        market, "intraday",
        f"{qty:g} {word}. About ₹{(margin if margin_mult < 1 else notional):,.0f} of the book. Stop near ₹{stop:,.2f}.",
        False,
    )
