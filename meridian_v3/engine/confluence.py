"""Confluence scoring across multiple independent factors.

Each factor votes in [-1, +1] with a weight. The desk score is 0–100.

    s = 50 + 50 * tanh(Σ w_i x_i)

High confluence is required before a paper trade can be promoted live.
"""

from __future__ import annotations

import math
from dataclasses import dataclass


@dataclass(frozen=True)
class FactorVote:
    name: str
    value: float
    weight: float
    note: str = ""


@dataclass(frozen=True)
class Confluence:
    score: float
    side: int
    votes: tuple[FactorVote, ...]
    reason: str


def score_confluence(votes: list[FactorVote]) -> Confluence:
    num = 0.0
    den = 0.0
    bits: list[str] = []
    for vote in votes:
        v = max(-1.0, min(1.0, vote.value))
        w = max(0.0, vote.weight)
        num += w * v
        den += w
        if vote.note:
            bits.append(vote.note)
    raw = num / den if den else 0.0
    score = 50.0 + 50.0 * math.tanh(raw)
    if raw > 0.15:
        side = 1
    elif raw < -0.15:
        side = -1
    else:
        side = 0
    reason = "; ".join(bits) or "factors are mixed"
    return Confluence(score=round(score, 1), side=side, votes=tuple(votes), reason=reason)
