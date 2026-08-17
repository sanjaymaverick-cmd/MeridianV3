"""Capital & Position Sizing Engine.

Combines:
  1. confidence-weighted fractional Kelly
  2. ATR volatility normalization
  3. drawdown scale
  4. ₹50,000 cash / lot / market constraints
  5. compounding (the whole book is the bankroll)
"""

from __future__ import annotations

import math

from dataclasses import dataclass

from meridian_v3.config import Settings, get_settings
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
    # Price-units the clip represents. For most markets this equals `qty`,
    # but a contract market quotes `qty` in *lots*: one FX lot is 100,000
    # units of base currency, one India mini-future lot is 65. `decide()`
    # multiplies per-unit win/loss/cost figures by this, so conflating the
    # two understated an FX clip by 100,000x and left every forex signal
    # stranded behind the edge filter's absolute floor. Defaults to 0.0,
    # which callers read as "same as qty".
    units: float = 0.0


def stop_price(side: str, entry: float, stop: float, settings: Settings | None = None) -> float:
    """Turn an ATR *distance* into a price line.

    ``SizePlan.stop`` is rupees of room (ATR × k), not a level. A silver
    short at ₹6,216 with ₹219 of room must stop at ₹6,435 — never treat
    ₹219 as the stop price or every tick looks like a stop-out.

    The 0.45 boundary that used to be hardcoded here — "a distance this
    large relative to entry is already a price line, not ATR room" — is
    ``sizing.stop_distance_ratio_ceiling`` (2.9), so it's a config knob
    rather than a magic number buried in this function.
    """
    dist = float(stop or 0.0)
    px = float(entry or 0.0)
    if dist <= 0 or px <= 0:
        return dist
    settings = settings or get_settings()
    if dist > px * settings.sizing.stop_distance_ratio_ceiling:
        return dist
    if side == "sell":
        return px + dist
    return max(0.0, px - dist)


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
    market_open_count: int = 0,
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
    # A real floor, not a sliding one. This used to be
    #   min(equity * cash_reserve_pct, cash * 0.35)
    # so the reserve shrank with the cash it was meant to protect: at ₹71k
    # cash it held back ₹9,846, but at ₹300 cash it held back ₹105. Each
    # successive clip in a cycle saw a smaller floor, and the book ran itself
    # down to ₹106 (0.11% of equity) in a single pass while the setting said
    # 10%. Below the reserve, `spendable` is 0 and no new clip opens — which
    # is what a reserve is for.
    reserve = equity * settings.sizing.cash_reserve_pct
    spendable = max(0.0, cash - reserve)
    risk_rupees = max(settings.sizing.min_risk_inr, equity * risk_pct)
    max_notional = min(spendable, equity * settings.sizing.max_position_pct)

    # Two ceilings, checked in order. `market_open_count` is this market's
    # own tally and is what normally binds; `open_count` is the whole book's
    # and is only a backstop. A single global count used to be the sole
    # control, which let nine crypto clips and four commodities veto all 90
    # Indian equity symbols — sleeves that share no session, no cost
    # structure and no risk driver.
    high = confidence >= settings.sizing.high_confidence
    spec = getattr(settings.markets, market, None)
    per_market = getattr(spec, "max_concurrent", None) if spec is not None else None
    if per_market is not None and market_open_count >= per_market:
        return SizePlan(
            0, 0, 0, risk_pct, kelly.sized, 0, market, "intraday",
            f"Already {market_open_count} open {market} clip(s), the limit for this market. "
            "Other markets are unaffected.",
            True,
        )
    max_open = settings.sizing.max_concurrent_high if high else settings.sizing.max_concurrent_normal
    if open_count >= max_open:
        return SizePlan(
            0, 0, 0, risk_pct, kelly.sized, 0, market, "intraday",
            f"Already {open_count} open clips across the whole book. Wait — the book is full enough.",
            True,
        )

    if market == "options_buy":
        spec = settings.markets.options_buy
        stop_pct = max(1e-6, min(1.0, spec.stop_pct_of_premium))
        # The premium ceiling is whichever is tighter: the explicit cap, or
        # the premium at which a stop-out costs exactly the normal risk
        # budget. Without the second term a 12%-of-equity premium at a 50%
        # stop risks 6% of the book on one clip, against a 1.5% cap
        # everywhere else on the desk.
        risk_implied_cap = risk_rupees / stop_pct
        prem_cap = min(equity * spec.max_premium_pct_of_equity, risk_implied_cap)
        qty = 1.0 if price <= min(max_notional, prem_cap) else 0.0
        if qty <= 0:
            return SizePlan(
                0, 0, 0, risk_pct, kelly.sized, price, market, "intraday",
                "Option premium does not fit the ₹50,000 book. Options buying only, and only a small premium.",
                True,
            )
        # Stop is rupees of room, as everywhere else — a fraction of premium
        # rather than the whole of it, so an option can be cut before it is
        # worthless and the modelled loss matches the real one.
        stop_room = price * stop_pct
        return SizePlan(
            qty, price, min(stop_room, risk_rupees), risk_pct, kelly.sized, stop_room,
            market, "intraday",
            f"One option lot. Premium ₹{price:,.0f}, stop at −{stop_pct:.0%} of it. "
            "Buying only — never selling premium on this book.",
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
            units=lots * contract,
        )

    if market == "global_commodities":
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
            word="global commodity (USD mark × USDINR, full rupee notional)",
        )

    if market == "forex_micro":
        spec = settings.markets.forex_micro
        # A lot of 1.0 is `contract_size` units of the base currency (100,000
        # for FX). This used to read `min_lot` as raw units and hand back a
        # fixed 0.01-unit clip — ₹0.84 of USDINR — regardless of equity,
        # confidence or edge. Nothing that small can clear a safety pad, so
        # forex never took a trade in five years of backtest.
        per_lot = spec.contract_size * price
        stop_room = atr * settings.sizing.atr_stop_mult
        risk_per_lot = spec.contract_size * stop_room
        if per_lot <= 0 or risk_per_lot <= 0:
            return SizePlan(
                0, 0, 0, risk_pct, kelly.sized, 0, market, "intraday",
                "No usable FX price or ATR for sizing.", True,
            )

        # Sized by risk like every other market, then held under whichever
        # cap binds first: cash on hand, the per-position ceiling, or the
        # standard-lot ban.
        lots = min(risk_rupees / risk_per_lot, max_notional / per_lot)
        step = spec.lot_step or spec.min_lot
        lots = math.floor(lots / step) * step
        ceiling = spec.standard_lot_qty - step if spec.standard_lots_forbidden else lots
        lots = min(lots, ceiling)

        if lots < spec.min_lot or spec.min_lot * per_lot > max_notional:
            return SizePlan(
                0, 0, 0, risk_pct, kelly.sized, 0, market, "intraday",
                "Even a nano lot is too large for cash on hand.",
                True,
            )
        notional = lots * per_lot
        return SizePlan(
            lots, notional, lots * risk_per_lot, risk_pct, kelly.sized, stop_room,
            market, "intraday",
            f"Forex nano/micro only: {lots:g} lot ({lots * spec.contract_size:,.0f} units, "
            f"₹{notional:,.0f}). Standard lots are forbidden. "
            f"FX runs Sunday–Friday, including after the NSE close.",
            False,
            units=lots * spec.contract_size,
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
    if market == "crypto_futures":
        spec = settings.markets.crypto_futures
    elif market == "global_commodities":
        spec = settings.markets.global_commodities
    else:
        spec = settings.markets.crypto_spot
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
