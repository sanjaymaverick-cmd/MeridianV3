"""Bayesian / online update of signal confidence from paper results.

Each rule starts with a Beta(α, β) prior. After each closed paper
trade we add a win or a loss.

    p_hat = (α + wins) / (α + β + n)

That p_hat feeds Kelly and the live gate. A rule that has been wrong
lately quietly shrinks. A rule that has been right lately earns more
of the ₹5,000 book.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class BetaBelief:
    alpha: float
    beta: float
    wins: int
    losses: int

    @property
    def n(self) -> int:
        return self.wins + self.losses

    @property
    def mean(self) -> float:
        return self.alpha / (self.alpha + self.beta)

    @property
    def strength(self) -> float:
        return self.alpha + self.beta


def start_belief(*, prior_p: float = 0.5, strength: float = 8.0) -> BetaBelief:
    prior_p = min(0.9, max(0.1, prior_p))
    return BetaBelief(alpha=prior_p * strength, beta=(1.0 - prior_p) * strength, wins=0, losses=0)


def update_belief(belief: BetaBelief, won: bool) -> BetaBelief:
    if won:
        return BetaBelief(
            alpha=belief.alpha + 1.0,
            beta=belief.beta,
            wins=belief.wins + 1,
            losses=belief.losses,
        )
    return BetaBelief(
        alpha=belief.alpha,
        beta=belief.beta + 1.0,
        wins=belief.wins,
        losses=belief.losses + 1,
    )


def blend_confidence(model_confidence: float, belief: BetaBelief, *, weight: float = 0.45) -> float:
    """Shrink a one-shot model score toward the paper track record."""
    w = min(1.0, max(0.0, weight))
    blended = (1.0 - w) * model_confidence + w * belief.mean
    return min(0.97, max(0.05, blended))
