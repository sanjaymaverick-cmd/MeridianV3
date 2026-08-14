"""Plain-language reviews. Math is already done. This file only writes sentences."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from meridian_v3.domain.copy import assert_review_copy
from meridian_v3.domain.templates import CATALOG, CHOICES
from meridian_v3.risk.greeks_book import GreekSnapshot
from meridian_v3.risk.vega_strategies import VegaAction


class ReviewScenario(str, Enum):
    BALANCED = "balanced"
    LONG_GAMMA_THETA_COST = "long_gamma_theta_cost"
    SHORT_GAMMA_THETA_BENEFIT = "short_gamma_theta_benefit"
    SHORT_GAMMA_NEGATIVE_THETA = "short_gamma_negative_theta"
    OVER_LIMIT_VEGA = "over_limit_vega"
    QUIET_MARKET = "quiet_market"
    RESIDUAL_DELTA = "residual_delta"
    COMBINED = "combined"
    GAMMA_SCALP = "gamma_scalp"
    VEGA_ACTION = "vega_action"
    AUTO_DECISION = "auto_decision"
    DRAWDOWN_PAUSE = "drawdown_pause"
    IMPORTED_BOOK = "imported_book"


TITLES = {key: item["title"] for key, item in CATALOG.items()}


@dataclass(frozen=True)
class PlainReview:
    title: str
    scenario: ReviewScenario
    status_lines: list[str]
    daily_pnl_line: str
    gamma_scalp_line: str
    suggestion: str
    choices: list[str] = field(default_factory=lambda: list(CHOICES))

    @property
    def body(self) -> str:
        parts = [self.title, *self.status_lines, self.daily_pnl_line, self.gamma_scalp_line, self.suggestion]
        parts.append("You can: " + "; ".join(self.choices) + ".")
        return assert_review_copy(" ".join(parts))

    def as_dict(self) -> dict:
        return {
            "title": self.title,
            "scenario": self.scenario.value,
            "status": self.status_lines,
            "daily_pnl": self.daily_pnl_line,
            "gamma_scalp_pnl": self.gamma_scalp_line,
            "suggestion": self.suggestion,
            "choices": self.choices,
            "body": self.body,
        }


def _rupees(value: float) -> str:
    sign = "+" if value > 0 else ""
    return f"{sign}₹{value:,.0f}"


def _status(snap: GreekSnapshot) -> list[str]:
    if snap.gamma_sign == "long":
        gamma_words = "Long gamma — a move can help if you stay hedged"
    elif snap.gamma_sign == "short":
        gamma_words = "Short gamma — a move can hurt"
    else:
        gamma_words = "Gamma is flat"
    delta_words = "almost flat" if abs(snap.delta_lots) < 0.25 else f"{snap.delta_lots:+.2f} lots of leftover direction"
    vega_words = "flat" if abs(snap.vega) < 1 else f"{snap.vega:,.0f} ₹ for each 1 point of vol"
    if snap.theta < -1:
        theta_words = f"you pay about ₹{abs(snap.theta):,.0f} each day as time passes"
    elif snap.theta > 1:
        theta_words = f"you collect about ₹{snap.theta:,.0f} each day as time passes"
    else:
        theta_words = "time is not doing much today"
    return [
        f"Delta: {delta_words} ({snap.delta_lots:+.2f} lots).",
        f"Gamma: {gamma_words} ({snap.gamma:+.4f}).",
        f"Vega: {vega_words}.",
        f"Theta: {theta_words} ({snap.theta:+,.0f} ₹/day).",
    ]


def _daily(snap: GreekSnapshot) -> str:
    if snap.daily_pnl < -1:
        return (
            f"Daily PnL: about {_rupees(snap.daily_pnl)} today if the market sits still "
            "(this is mostly time decay)."
        )
    if snap.daily_pnl > 1:
        return (
            f"Daily PnL: about {_rupees(snap.daily_pnl)} today if the market sits still "
            "(time is paying you)."
        )
    return "Daily PnL: near zero if the market sits still."


def _scalp(snap: GreekSnapshot) -> str:
    move = snap.move_pct * 100
    amount = _rupees(snap.gamma_scalp_pnl)
    if snap.scalp_helps:
        return (
            f"Gamma Scalping PnL: a {move:.1f}% move could add about {amount} "
            "if you keep delta small (gamma scalping is helping)."
        )
    if snap.short_gamma:
        return (
            f"Gamma Scalping PnL: a {move:.1f}% move could cost about {amount} "
            "(short gamma — this is the opposite of a scalp, and it hurts)."
        )
    return "Gamma Scalping PnL: near zero on a small move (gamma is flat)."


def pick_scenario(
    snap: GreekSnapshot,
    *,
    vega_limit: float | None = None,
    quiet: bool = False,
) -> ReviewScenario:
    over = vega_limit is not None and abs(snap.vega) > vega_limit + 1e-6
    if over:
        return ReviewScenario.OVER_LIMIT_VEGA
    if snap.short_gamma and snap.theta < -1:
        return ReviewScenario.SHORT_GAMMA_NEGATIVE_THETA
    if snap.short_gamma and snap.theta > 1:
        return ReviewScenario.SHORT_GAMMA_THETA_BENEFIT
    if snap.long_gamma and snap.theta < -1:
        return ReviewScenario.LONG_GAMMA_THETA_COST
    if abs(snap.delta_lots) >= 1.0 and abs(snap.gamma) < 1e-12:
        return ReviewScenario.RESIDUAL_DELTA
    if quiet or (abs(snap.gamma_scalp_pnl) + 1 < abs(snap.daily_pnl) and abs(snap.daily_pnl) > 1):
        return ReviewScenario.QUIET_MARKET
    return ReviewScenario.BALANCED


def _suggestion(snap: GreekSnapshot, scenario: ReviewScenario, model_lots: float | None) -> str:
    if model_lots is None or abs(model_lots) < 1e-12:
        model = "The model has no lot step right now."
    else:
        side = "sell" if model_lots < 0 else "buy"
        model = f"Model suggestion: {side} {abs(model_lots):.1f} lots (review only)."
    extra = CATALOG[scenario.value]["plain"]
    return f"{model} {extra}"


def compose_review(
    snap: GreekSnapshot,
    *,
    scenario: ReviewScenario | None = None,
    vega_limit: float | None = None,
    model_lots: float | None = None,
    quiet: bool = False,
    action: VegaAction | None = None,
) -> PlainReview:
    if action is not None:
        scenario = ReviewScenario.VEGA_ACTION
        model_lots = action.opt_lots
    if scenario is None:
        scenario = pick_scenario(snap, vega_limit=vega_limit, quiet=quiet)
    suggestion = _suggestion(snap, scenario, model_lots)
    if action is not None:
        suggestion = (
            f"Model suggestion: {action.title.lower()} by {action.opt_lots:+.1f} lots. "
            f"That would also move delta {action.d_delta:+.2f} lots, gamma {action.d_gamma:+.4f}, "
            f"vega {action.d_vega:+,.0f}, theta {action.d_theta:+,.0f} ₹/day. {action.note}"
        )
    review = PlainReview(
        title=TITLES[scenario.value],
        scenario=scenario,
        status_lines=_status(snap),
        daily_pnl_line=_daily(snap),
        gamma_scalp_line=_scalp(snap),
        suggestion=suggestion,
    )
    assert_review_copy(review.body)
    return review


def compose_decision_review(
    *,
    symbol: str,
    action: str,
    confidence: float,
    confluence: float,
    market: str,
    paper_only: bool,
    extra: str = "",
) -> PlainReview:
    side = action.upper()
    status = [
        f"{symbol}: the auto engine leans {side} with confidence {confidence:.0%}.",
        f"Confluence across factors is {confluence:.0f} out of 100.",
        f"Preferred market for this clip is {market.replace('_', ' ')}.",
        "This card is a review of the decision. It is not the live ticket.",
    ]
    suggestion = extra or CATALOG["auto_decision"]["plain"]
    if paper_only:
        suggestion = "A paper trade is written. Live is not armed for this clip. " + suggestion
    review = PlainReview(
        title=TITLES["auto_decision"],
        scenario=ReviewScenario.AUTO_DECISION,
        status_lines=status,
        daily_pnl_line="Daily PnL: not the main story on this card — see the Greeks page for time.",
        gamma_scalp_line="Gamma Scalping PnL: not the main story on this card.",
        suggestion=suggestion,
    )
    assert_review_copy(review.body)
    return review
