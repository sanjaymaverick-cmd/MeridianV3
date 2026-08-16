"""Meta-labeling.

Primary model: direction in {-1, 0, +1}.
Secondary model: P(the primary call is right | features).

We do not send the primary call live unless the secondary probability
clears the bar. This is the López de Prado pattern, kept small enough
for a ₹5,000 book.

The logistic here is a transparent online model — no pickled black box.
Coefficients stay in Python. The UI only sees p_success.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Sequence


def _sigmoid(z: float) -> float:
    z = max(-30.0, min(30.0, z))
    return 1.0 / (1.0 + math.exp(-z))


@dataclass
class PrimarySignal:
    direction: int  # -1, 0, +1
    raw_score: float
    reason: str


@dataclass
class MetaLabel:
    direction: int
    p_success: float
    take: bool
    features: dict[str, float] = field(default_factory=dict)
    reason: str = ""


def primary_direction(
    *,
    last: float,
    sma_fast: float | None,
    sma_slow: float | None,
    rsi: float | None,
    breakout: bool,
    mean_revert: bool,
    regime: str,
) -> PrimarySignal:
    score = 0.0
    bits: list[str] = []
    if sma_fast and sma_slow:
        if sma_fast > sma_slow:
            score += 1.0
            bits.append("fast average is above the slow one")
        elif sma_fast < sma_slow:
            score -= 1.0
            bits.append("fast average is below the slow one")
    if last and sma_fast:
        if last > sma_fast:
            score += 0.5
        else:
            score -= 0.5
    if rsi is not None:
        if rsi < 30:
            score += 0.4
            bits.append("it looks washed out")
        elif rsi > 70:
            score -= 0.4
            bits.append("it looks stretched")
    if breakout:
        score += 0.8
        bits.append("price pushed through a recent high with life")
    if mean_revert and regime != "Stress":
        score += 0.3
        bits.append("it is near a recent low")
    if regime == "Stress":
        score *= 0.6
        bits.append("the market mood is stressed, so the call is quieter")
    if score > 0.6:
        direction = 1
    elif score < -0.6:
        direction = -1
    else:
        direction = 0
    reason = "; ".join(bits) or "no clear picture"
    return PrimarySignal(direction=direction, raw_score=score, reason=reason)


class OnlineLogit:
    """Tiny online logistic for P(success). Updated from paper outcomes."""

    def __init__(self, lr: float = 0.08) -> None:
        self.lr = lr
        # Seeded with the cold-start formula rather than zeros. This used to
        # start at bias=0 / no weights, so the very first update threw the
        # cold-start prior away and dropped straight to sigmoid(0) = 0.50 —
        # a cliff from ~0.76 on a strong signal. One losing trade then took
        # p_success to 0.43, under the 0.55 meta-label floor, and the desk
        # could never trade again to earn its way back: a one-way trapdoor
        # that shut the whole book after a single loss.
        #
        # Expressed as a plain linear model, `_cold_start_p`'s z is
        #   0.20 + 0.90(conf-0.5) + 0.55*primary + 0.25(fresh-0.5) + 0.15(cheap-0.5)
        # = -0.45 + 0.90*conf + 0.55*primary + 0.25*fresh + 0.15*cheap
        # so seeding those coefficients makes `predict` continuous across the
        # first update, and learning refines the prior instead of discarding it.
        self.bias = _COLD_START_BIAS
        self.weights: dict[str, float] = dict(_COLD_START_WEIGHTS)
        self.updates = 0

    @property
    def trained(self) -> bool:
        return self.updates > 0

    def predict(self, features: dict[str, float]) -> float:
        z = self.bias
        for key, value in features.items():
            z += self.weights.get(key, 0.0) * value
        return _sigmoid(z)

    def update(self, features: dict[str, float], won: bool) -> float:
        y = 1.0 if won else 0.0
        p = self.predict(features)
        err = y - p
        self.bias += self.lr * err
        for key, value in features.items():
            self.weights[key] = self.weights.get(key, 0.0) + self.lr * err * value
        self.updates += 1
        return self.predict(features)


# The cold-start prior, in linear form. `OnlineLogit` is seeded with these so
# its first update refines the prior rather than replacing it — see __init__.
_COLD_START_BIAS = 0.20 - 0.90 * 0.5 - 0.25 * 0.5 - 0.15 * 0.5
_COLD_START_WEIGHTS: dict[str, float] = {
    "confluence": 0.90,
    "primary": 0.55,
    "freshness": 0.25,
    "cheap": 0.15,
}


def _cold_start_p(features: dict[str, float]) -> float:
    """A new book has no paper history. Read the features instead of saying 50/50."""
    z = 0.20
    z += 0.90 * (features.get("confluence", 0.5) - 0.5)
    z += 0.55 * features.get("primary", 0.0)
    z += 0.25 * (features.get("freshness", 1.0) - 0.5)
    z += 0.15 * (features.get("cheap", 0.5) - 0.5)
    return _sigmoid(z)


def default_features(
    *,
    confluence: float,
    freshness: float,
    atr_pct: float,
    regime_fit: float,
    primary_score: float,
    cost_pct: float,
) -> dict[str, float]:
    return {
        "confluence": confluence / 100.0,
        "freshness": freshness,
        "atr_pct": min(atr_pct, 0.08) / 0.08,
        "regime_fit": regime_fit,
        "primary": max(-1.0, min(1.0, primary_score / 3.0)),
        "cheap": 1.0 - min(cost_pct / 0.01, 1.0),
    }


def meta_label(
    primary: PrimarySignal,
    features: dict[str, float],
    model: OnlineLogit | None = None,
    *,
    min_p: float = 0.55,
) -> MetaLabel:
    if primary.direction == 0:
        return MetaLabel(
            direction=0,
            p_success=0.5,
            take=False,
            features=features,
            reason="Primary model says hold — no side to bet.",
        )
    mdl = model or OnlineLogit()
    p = mdl.predict(features)
    take = p >= min_p
    reason = (
        f"Primary side is {'long' if primary.direction > 0 else 'short'}. "
        f"Chance it works is {p:.0%}. "
        + ("We take the paper trade." if take else "We skip — the second model is not sure enough.")
    )
    return MetaLabel(direction=primary.direction, p_success=p, take=take, features=features, reason=reason)


def rsi(closes: Sequence[float], period: int = 14) -> float | None:
    if len(closes) < period + 1:
        return None
    gains = 0.0
    losses = 0.0
    for i in range(-period, 0):
        change = closes[i] - closes[i - 1]
        if change >= 0:
            gains += change
        else:
            losses -= change
    if losses == 0:
        return 100.0
    rs = (gains / period) / (losses / period)
    return 100.0 - (100.0 / (1.0 + rs))
