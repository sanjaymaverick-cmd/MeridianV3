"""Auto Decision Engine.

Final Buy / Sell / Hold with confidence gating.

Pipeline (every signal):
  primary direction → confluence → freshness → meta-label p
  → Bayesian blend → cost-aware edge → size → safety
  → always paper → live only if the high bar is cleared.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone

from meridian_v3.capital.sizer import SizePlan, size_position
from meridian_v3.config import Settings, get_settings
from meridian_v3.domain.reviews import PlainReview, compose_decision_review
from meridian_v3.engine.bayesian import BetaBelief, blend_confidence, start_belief
from meridian_v3.engine.confluence import Confluence, FactorVote, score_confluence
from meridian_v3.engine.drawdown import DrawdownState
from meridian_v3.engine.edge import CostEstimate, filter_edge
from meridian_v3.engine.freshness import apply_freshness, freshness
from meridian_v3.engine.meta_label import MetaLabel, OnlineLogit, PrimarySignal, default_features, meta_label
from meridian_v3.engine.walkforward import Robustness
from meridian_v3.router.markets import Route, route_market
from meridian_v3.safety.guards import SafetyVerdict, evaluate_safety


@dataclass
class DecisionInput:
    symbol: str
    price: float
    atr: float
    created_at: datetime
    primary: PrimarySignal
    votes: list[FactorVote]
    win_rupees: float
    loss_rupees: float
    costs: CostEstimate
    payoff: float
    equity: float
    cash: float
    drawdown: DrawdownState
    live_armed: bool
    live_today: int
    open_count: int
    equity_score: float
    options_score: float
    forex_score: float
    now: datetime | None = None
    belief: BetaBelief | None = None
    logit: OnlineLogit | None = None
    robustness: Robustness | None = None
    options_allowed: bool = True
    forex_allowed: bool = True
    held_qty: float = 0.0


@dataclass
class AutoDecision:
    symbol: str
    action: str
    side: str
    confidence: float
    p_success: float
    confluence: float
    freshness: float
    market: str
    paper: bool
    live: bool
    size: SizePlan
    safety: SafetyVerdict
    review: PlainReview
    reasons: list[str] = field(default_factory=list)
    route: Route | None = None
    meta: MetaLabel | None = None


def decide(inp: DecisionInput, settings: Settings | None = None) -> AutoDecision:
    settings = settings or get_settings()
    now = inp.now or datetime.now(timezone.utc)
    reasons: list[str] = []

    conf = score_confluence(inp.votes)
    fresh = freshness(
        inp.created_at,
        now,
        half_life_hours=settings.decision.freshness_half_life_hours,
        floor=settings.decision.min_freshness,
    )
    features = default_features(
        confluence=conf.score,
        freshness=fresh.value,
        atr_pct=(inp.atr / inp.price) if inp.price else 0.02,
        regime_fit=0.6,
        primary_score=inp.primary.raw_score,
        cost_pct=(inp.costs.total / max(inp.win_rupees, 1.0)),
    )
    meta = meta_label(inp.primary, features, inp.logit, min_p=0.52)
    belief = inp.belief or start_belief()
    model_c = apply_freshness(meta.p_success, fresh)
    confidence = blend_confidence(model_c, belief)

    if inp.robustness is not None and not inp.robustness.robust:
        confidence *= 0.7
        reasons.append(inp.robustness.reason)

    route = route_market(
        equity_score=inp.equity_score,
        options_score=inp.options_score,
        forex_score=inp.forex_score,
        options_allowed=inp.options_allowed,
        forex_allowed=inp.forex_allowed,
    )

    action = "hold"
    if inp.primary.direction > 0 and conf.side >= 0 and meta.take and not fresh.stale:
        action = "buy"
    elif inp.primary.direction < 0 and conf.side <= 0 and meta.take and not fresh.stale:
        action = "sell"

    if action == "sell" and route.market == "equity_cash" and inp.held_qty <= 1e-9:
        action = "hold"
        reasons.append(
            "This cash book does not short. A sell is only used to close a paper long we already hold."
        )

    paper_margin = max(settings.decision.edge_safety_margin * inp.equity * 0.25, 2.0)
    edge = filter_edge(
        p=meta.p_success,
        win_rupees=inp.win_rupees,
        loss_rupees=inp.loss_rupees,
        costs=inp.costs,
        margin=paper_margin,
    )
    if action != "hold" and not edge.take:
        action = "hold"
        reasons.append(edge.reason)
    reasons.append(meta.reason)

    if fresh.stale:
        action = "hold"
        reasons.append(
            f"The signal is {fresh.age_hours:.1f} hours old. Freshness is {fresh.value:.0%}. We wait."
        )

    size = size_position(
        equity=inp.equity,
        cash=inp.cash,
        price=inp.price,
        atr=inp.atr,
        p_success=meta.p_success,
        payoff=inp.payoff,
        confidence=confidence,
        drawdown=inp.drawdown,
        settings=settings,
        market=route.market,
        open_count=inp.open_count,
    )
    if size.blocked and action != "hold":
        action = "hold"
        reasons.append(size.reason)

    safety = evaluate_safety(
        drawdown=inp.drawdown,
        settings=settings,
        live_armed=inp.live_armed,
        live_today=inp.live_today,
        confidence=confidence,
        market=route.market,
        horizon=size.horizon,
        now=now,
    )

    paper = settings.decision.paper_all_signals and action != "hold" and not size.blocked
    live = (
        paper
        and safety.allow_live
        and confidence >= settings.decision.live_min_confidence
        and conf.score >= settings.decision.live_min_confluence
    )
    if paper and not live:
        reasons.append(
            "Paper trade is written. Live stays closed unless confidence, confluence, "
            "and every safety light are high."
        )
    if live:
        reasons.append("This clip also clears the live gate. A live ticket is allowed.")
    if action == "hold":
        reasons.append("Hold. The book stays still.")

    review = compose_decision_review(
        symbol=inp.symbol,
        action=action,
        confidence=confidence,
        confluence=conf.score,
        market=route.market,
        paper_only=paper and not live,
        extra=" ".join(reasons[-2:]),
    )
    return AutoDecision(
        symbol=inp.symbol,
        action=action,
        side=action,
        confidence=confidence,
        p_success=meta.p_success,
        confluence=conf.score,
        freshness=fresh.value,
        market=route.market,
        paper=paper,
        live=live,
        size=size,
        safety=safety,
        review=review,
        reasons=reasons,
        route=route,
        meta=meta,
    )
