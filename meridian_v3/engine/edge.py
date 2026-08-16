"""Cost-aware expected-edge filter.

Trade only when expected edge beats estimated costs plus a safety
margin. On a ₹5,000 book, brokerage, STT, slippage and spread eat a
real slice of every clip.

    edge   = p * win - (1 - p) * loss
    costs  = brokerage + stt + slippage + spread
    take   = edge > costs + margin
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class CostEstimate:
    brokerage: float
    stt: float
    slippage: float
    spread: float

    @property
    def total(self) -> float:
        return self.brokerage + self.stt + self.slippage + self.spread


@dataclass(frozen=True)
class EdgeDecision:
    edge: float
    costs: float
    margin: float
    take: bool
    reason: str


def estimate_equity_costs(
    *,
    notional: float,
    brokerage_pct: float = 0.0003,
    stt_pct: float = 0.001,
    slippage_pct: float = 0.0008,
    spread_pct: float = 0.0004,
) -> CostEstimate:
    return CostEstimate(
        brokerage=notional * brokerage_pct,
        stt=notional * stt_pct,
        slippage=notional * slippage_pct,
        spread=notional * spread_pct,
    )


# Slippage and spread are execution frictions, not statutory charges, so
# `charges/indian.py:levy()` (the contract-note calculator) doesn't model
# them. These per-market rates cover that gap. Crypto/FX quote tighter than
# an illiquid option but wider than large-cap cash equity.
_SLIPPAGE_PCT = {
    "equity_cash": 0.0008,
    "india_futures": 0.0006,
    "global_commodities": 0.0006,
    "crypto_spot": 0.0010,
    "crypto_futures": 0.0010,
    "forex_micro": 0.0003,
    "options_buy": 0.0100,
    "crypto_options": 0.0100,
}
_SPREAD_PCT = {
    "equity_cash": 0.0004,
    "india_futures": 0.0003,
    "global_commodities": 0.0004,
    "crypto_spot": 0.0005,
    "crypto_futures": 0.0005,
    "forex_micro": 0.0002,
    "options_buy": 0.0080,
    "crypto_options": 0.0080,
}


def estimate_trade_costs(
    *,
    qty: float,
    price: float,
    market: str,
    broker: str = "zerodha",
    product: str = "CNC",
    round_trip: bool = True,
) -> CostEstimate:
    """Real, market-aware costs for an *actual* position.

    Uses ``charges/indian.py:levy()`` — the same contract-note calculator
    that bills every real fill — as the single source of truth for
    statutory charges, then adds slippage/spread which levy doesn't model.

    This replaces the old ``estimate_equity_costs(notional=price)`` call in
    the pre-trade gate, which had two defects:

    1. It passed the price of **one unit** as the position notional, so the
       cost estimate tracked an instrument's unit price rather than the
       size actually being traded. Measured against real fees on the real
       sizes: BTCUSDT was overstated ~2,236x (₹15,051 vs ₹6.73), ETHUSDT
       ~50x, while LINKUSDT was *under*stated (₹2.26 vs ₹6.25).
    2. It applied STT — an Indian *securities* transaction tax — at a flat
       0.1% to every market, including crypto and forex, which don't pay
       it. ``levy()`` already gets this right per market (see ``_kind``).
    """
    from meridian_v3.charges.indian import levy

    legs = ("buy", "sell") if round_trip else ("buy",)
    bills = [levy(broker=broker, market=market, side=s, qty=qty, price=price, product=product) for s in legs]
    notional = abs(qty) * abs(price)
    n = len(legs)
    return CostEstimate(
        brokerage=sum(b.brokerage + b.gst for b in bills),
        # Everything statutory that isn't brokerage/GST, folded into the
        # existing four-field shape rather than widening CostEstimate.
        stt=sum(b.stt + b.exchange + b.sebi + b.stamp + b.tds for b in bills),
        slippage=notional * _SLIPPAGE_PCT.get(market, 0.0008) * n,
        spread=notional * _SPREAD_PCT.get(market, 0.0004) * n,
    )


def round_trip_cost_pct(market: str, *, notional: float = 100_000.0, broker: str = "zerodha") -> float:
    """Round-trip cost as a fraction of notional, for one market.

    Costs vary by ~48x across the markets this desk trades: global
    commodities and forex round-trip at ~0.047%, equity at ~0.222%, and
    crypto at ~2.236% — the last almost entirely India's 1% VDA TDS, which
    is charged on *each* transfer, so a USDT-quoted pair (a VDA-to-VDA
    trade) pays it on both legs. A target that is generous on a commodity
    can be arithmetically unreachable on a coin.
    """
    est = estimate_trade_costs(qty=1.0, price=notional, market=market, broker=broker, round_trip=True)
    return est.total / notional if notional else 0.0


def estimate_option_costs(
    *,
    premium: float,
    brokerage: float = 20.0,
    stt_pct: float = 0.000625,
    slippage_pct: float = 0.01,
) -> CostEstimate:
    return CostEstimate(
        brokerage=brokerage,
        stt=premium * stt_pct,
        slippage=premium * slippage_pct,
        spread=premium * 0.008,
    )


def expected_edge(*, p: float, win_rupees: float, loss_rupees: float) -> float:
    p = min(0.999, max(0.001, p))
    return p * win_rupees - (1.0 - p) * abs(loss_rupees)


def filter_edge(
    *,
    p: float,
    win_rupees: float,
    loss_rupees: float,
    costs: CostEstimate,
    margin: float,
) -> EdgeDecision:
    edge = expected_edge(p=p, win_rupees=win_rupees, loss_rupees=loss_rupees)
    hurdle = costs.total + margin
    take = edge > hurdle
    if take:
        reason = (
            f"Expected extra money is ₹{edge:,.0f}. Costs plus a safety pad "
            f"are ₹{hurdle:,.0f}. The trade still has room."
        )
    else:
        reason = (
            f"Expected extra money is ₹{edge:,.0f}, but costs plus a safety pad "
            f"are ₹{hurdle:,.0f}. We skip. Small accounts cannot give money to the broker."
        )
    return EdgeDecision(edge=edge, costs=costs.total, margin=margin, take=take, reason=reason)
