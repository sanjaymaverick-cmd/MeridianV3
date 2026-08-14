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
