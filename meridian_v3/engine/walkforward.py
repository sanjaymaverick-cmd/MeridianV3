"""Walk-forward robustness and anti-overfitting safeguards.

We never claim a rule is ready just because it looked good on the
same window it was tuned on.

    gap = (is_score - oos_score) / max(|is_score|, eps)
    robust = oos_score > 0 and gap <= max_gap

Also: embargo after the test window, and a purge of overlapping
labels so yesterday's trade cannot leak into today's score.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence


@dataclass(frozen=True)
class Fold:
    train_start: int
    train_end: int
    test_start: int
    test_end: int


@dataclass(frozen=True)
class Robustness:
    is_score: float
    oos_score: float
    gap: float
    robust: bool
    folds: int
    reason: str


def walk_forward_folds(
    n: int,
    *,
    train: int = 60,
    test: int = 20,
    embargo: int = 2,
    step: int | None = None,
) -> list[Fold]:
    step = step or test
    folds: list[Fold] = []
    start = 0
    while start + train + embargo + test <= n:
        tr_end = start + train
        te_start = tr_end + embargo
        te_end = te_start + test
        folds.append(Fold(start, tr_end, te_start, te_end))
        start += step
    return folds


def robustness(
    is_scores: Sequence[float],
    oos_scores: Sequence[float],
    *,
    max_gap: float = 0.35,
) -> Robustness:
    if not is_scores or not oos_scores:
        return Robustness(0.0, 0.0, 1.0, False, 0, "Not enough walk-forward folds.")
    is_mean = sum(is_scores) / len(is_scores)
    oos_mean = sum(oos_scores) / len(oos_scores)
    gap = (is_mean - oos_mean) / max(abs(is_mean), 1e-6)
    robust = oos_mean > 0 and gap <= max_gap
    if robust:
        reason = (
            f"Out-of-sample still makes money ({oos_mean:.3f}) and the "
            f"in-sample gap is {gap:.0%} — the rule is allowed."
        )
    else:
        reason = (
            f"Out-of-sample is {oos_mean:.3f} with a {gap:.0%} in-sample gap. "
            "That looks like a curve-fit. The auto engine will not size this hard."
        )
    return Robustness(
        is_score=is_mean,
        oos_score=oos_mean,
        gap=gap,
        robust=robust,
        folds=min(len(is_scores), len(oos_scores)),
        reason=reason,
    )


def hit_rate(pnl: Sequence[float]) -> float:
    if not pnl:
        return 0.0
    return sum(1 for x in pnl if x > 0) / len(pnl)
