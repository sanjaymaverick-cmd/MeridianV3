"""The pre-trade cost gate must price the *actual position*, not one unit.

Two defects found by investigating why a live overnight run took zero
trades despite 20,904 evaluated signals:

1. ``estimate_equity_costs(notional=cache.last)`` passed the price of a
   single unit as the position notional, so the estimate tracked an
   instrument's unit price rather than the size being traded. Against real
   fees on real sizes: BTCUSDT overstated ~2,236x, LINKUSDT *under*stated.
2. A flat 0.1% STT — an Indian *securities* transaction tax — was charged
   on every market including crypto and forex, which don't pay it.

Consequence: because the per-unit edge was compared against an absolute
rupee hurdle, the gate's verdict tracked unit price. RELIANCE at ~₹1,310
was skipped while BTCUSDT at ~₹60L passed, on identical assumptions.
"""

from __future__ import annotations

from meridian_v3.charges.indian import levy
from meridian_v3.engine.edge import estimate_equity_costs, estimate_trade_costs


def test_costs_track_position_size_not_unit_price():
    """Two positions of near-identical rupee notional must cost roughly the
    same, regardless of wildly different per-unit prices. The old model got
    this catastrophically wrong."""
    # ~₹6,000 of BTC (one unit = ₹60L) vs ~₹6,000 of LINK (one unit = ₹906)
    btc = estimate_trade_costs(qty=0.001, price=6_020_524, market="crypto_spot").total
    link = estimate_trade_costs(qty=6.6, price=906, market="crypto_spot").total
    assert abs(btc - link) / max(btc, link) < 0.10, f"btc={btc} link={link} should be within 10%"

    # The old model, on the same two positions, differed by >1000x purely
    # because of unit price.
    old_btc = estimate_equity_costs(notional=6_020_524).total
    old_link = estimate_equity_costs(notional=906).total
    assert old_btc / old_link > 1000


def test_no_stt_on_crypto_or_forex():
    """STT is a securities transaction tax. Charging it on crypto/FX was
    inflating those markets' modelled costs by ~40% of the total rate."""
    for market in ("crypto_spot", "crypto_futures", "forex_micro"):
        bill = levy(broker="zerodha", market=market, side="buy", qty=1, price=10_000, product="CNC")
        assert bill.stt == 0.0, f"{market} must not pay STT"

    # Equity still does pay it — this isn't a blanket removal.
    equity_bill = levy(broker="zerodha", market="equity_cash", side="buy", qty=1, price=10_000, product="CNC")
    assert equity_bill.stt > 0.0


def test_estimate_never_understates_the_real_contract_note():
    """The gate's estimate must be >= the statutory bill the fill will
    actually pay (it adds slippage/spread on top, which levy doesn't
    model). The old model *understated* low-unit-price instruments —
    LINKUSDT modelled at ₹2.26 against a real ₹6.25 fee."""
    cases = [
        (0.001, 6_020_524, "crypto_spot"),
        (7.1, 906, "crypto_spot"),
        (4, 1310, "equity_cash"),
        (0.0045, 179_634, "crypto_spot"),
    ]
    for qty, price, market in cases:
        modelled = estimate_trade_costs(qty=qty, price=price, market=market).total
        real = levy(broker="zerodha", market=market, side="buy", qty=qty, price=price, product="CNC").total
        assert modelled >= real, f"{market} qty={qty}: modelled {modelled} < real {real}"


def test_costs_scale_linearly_with_quantity():
    small = estimate_trade_costs(qty=1, price=1000, market="equity_cash").total
    big = estimate_trade_costs(qty=10, price=1000, market="equity_cash").total
    assert big > small
    assert 8.0 < big / small < 12.0  # ~10x, allowing for any fixed-fee component


def test_decide_gate_no_longer_depends_on_unit_price():
    """The end-to-end proof: two instruments with the same ATR *percentage*
    and same signal quality must get the same take/skip verdict, even when
    their unit prices differ by four orders of magnitude. Before the fix,
    the ₹1,310 instrument was skipped and the ₹6,020,524 one taken."""
    from datetime import datetime, timezone

    from meridian_v3.config import Settings
    from meridian_v3.decision.engine import DecisionInput, decide
    from meridian_v3.engine.confluence import FactorVote
    from meridian_v3.engine.drawdown import assess_drawdown
    from meridian_v3.engine.edge import estimate_equity_costs
    from meridian_v3.engine.meta_label import PrimarySignal

    def _verdict(price: float) -> bool:
        atr = price * 0.02  # same 2% ATR for both
        now = datetime.now(timezone.utc)
        d = decide(
            DecisionInput(
                symbol="X", price=price, atr=atr, created_at=now,
                primary=PrimarySignal(1, 1.4, "trend is up"),
                votes=[
                    FactorVote("trend", 0.8, 1.0, "up"),
                    FactorVote("breakout", 0.6, 0.8, "high"),
                    FactorVote("score", 0.5, 0.7, "quality"),
                ],
                win_rupees=atr * 2.0, loss_rupees=atr * 1.5,
                costs=estimate_equity_costs(notional=price),
                payoff=1.33, equity=100_000, cash=100_000,
                drawdown=assess_drawdown(100_000, 100_000),
                live_armed=False, live_today=0, open_count=0,
                preferred_market="crypto_spot", now=now,
            ),
            Settings(),
        )
        return d.action != "hold"

    cheap = _verdict(1310.0)
    dear = _verdict(6_020_524.0)
    assert cheap == dear, f"unit price still drives the verdict: ₹1,310 -> {cheap}, ₹60L -> {dear}"
