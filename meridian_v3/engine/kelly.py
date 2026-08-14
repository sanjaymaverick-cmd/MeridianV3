"""Confidence-weighted fractional Kelly.

Full Kelly is too aggressive for a ₹5,000 book. We take a small slice
and then shrink or grow that slice with the signal's confidence.

    f*  = (p * b - q) / b          # textbook Kelly fraction of bankroll
    f   = clip(κ * c * f*, 0, fmax)

p  = probability the trade works (from the meta-label model)
q  = 1 - p
b  = net win / net loss  (payoff ratio)
κ  = Kelly fraction (default 0.15)
c  = confidence in [0, 1]
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class KellyResult:
    full_kelly: float
    fractional: float
    sized: float
    p: float
    b: float
    kappa: float
    confidence: float
    reason: str


def full_kelly(p: float, b: float) -> float:
    if b <= 0:
        return 0.0
    p = min(0.999, max(0.001, p))
    q = 1.0 - p
    return (p * b - q) / b


def confidence_weighted_fractional_kelly(
    *,
    p: float,
    b: float,
    confidence: float,
    kappa: float = 0.15,
    fmin: float = 0.0,
    fmax: float = 0.25,
) -> KellyResult:
    raw = full_kelly(p, b)
    if raw <= 0:
        return KellyResult(
            full_kelly=raw,
            fractional=0.0,
            sized=0.0,
            p=p,
            b=b,
            kappa=kappa,
            confidence=confidence,
            reason="No edge after costs — Kelly is zero or negative.",
        )
    c = min(1.0, max(0.0, confidence))
    k = min(0.25, max(0.05, kappa))
    sized = min(fmax, max(fmin, k * c * raw))
    return KellyResult(
        full_kelly=raw,
        fractional=k * raw,
        sized=sized,
        p=p,
        b=b,
        kappa=k,
        confidence=c,
        reason=(
            f"Full Kelly {raw:.3f} × fraction {k:.2f} × confidence {c:.2f} "
            f"= {sized:.3f} of the book."
        ),
    )
