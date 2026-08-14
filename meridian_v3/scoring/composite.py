"""V1 five-factor composite. Algorithms stay the same."""

from __future__ import annotations

from decimal import Decimal

FACTORS = ("quality", "valuation", "technical", "ownership", "sentiment")

DEFAULT_WEIGHTS: dict[str, dict[str, float]] = {
    "Calm": {"quality": 0.28, "valuation": 0.22, "technical": 0.18, "ownership": 0.18, "sentiment": 0.14},
    "Elevated": {"quality": 0.26, "valuation": 0.16, "technical": 0.22, "ownership": 0.22, "sentiment": 0.14},
    "Stress": {"quality": 0.24, "valuation": 0.10, "technical": 0.26, "ownership": 0.26, "sentiment": 0.14},
}

DEFAULT_GATES: dict[str, dict[str, float]] = {
    "Calm": {"strong_buy": 8.0, "buy": 6.8, "hold": 5.0, "reduce": 3.8},
    "Elevated": {"strong_buy": 8.3, "buy": 7.1, "hold": 5.2, "reduce": 4.0},
    "Stress": {"strong_buy": 8.6, "buy": 7.4, "hold": 5.5, "reduce": 4.2},
}


def blend_weights(label: str = "Calm", soft: dict[str, float] | None = None) -> dict[str, Decimal]:
    if not soft:
        raw = DEFAULT_WEIGHTS.get(label, DEFAULT_WEIGHTS["Calm"])
        return {key: Decimal(str(raw[key])) for key in FACTORS}
    blended: dict[str, Decimal] = {}
    for factor in FACTORS:
        total = 0.0
        for mood, mass in soft.items():
            total += mass * DEFAULT_WEIGHTS.get(mood, DEFAULT_WEIGHTS["Calm"]).get(factor, 0.0)
        blended[factor] = Decimal(str(round(total, 4)))
    return blended


def composite_score(parts: dict[str, float | None], weights: dict[str, Decimal]) -> Decimal | None:
    total = Decimal("0")
    mass = Decimal("0")
    for factor, weight in weights.items():
        value = parts.get(factor)
        if value is None:
            continue
        total += Decimal(str(value)) * weight
        mass += weight
    if mass == 0:
        return None
    return (total / mass).quantize(Decimal("0.01"))


def map_action(score: Decimal | None, label: str = "Calm") -> str:
    if score is None:
        return "—"
    gates = DEFAULT_GATES.get(label, DEFAULT_GATES["Calm"])
    value = float(score)
    if value >= gates["strong_buy"]:
        return "Strong Buy"
    if value >= gates["buy"]:
        return "Buy"
    if value >= gates["hold"]:
        return "Hold"
    if value >= gates["reduce"]:
        return "Reduce"
    return "Sell"
