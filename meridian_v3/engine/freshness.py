"""Signal freshness / decay.

A signal that is hours old is not the same as a signal that just
printed. We decay confidence with a half-life:

    freshness = 0.5 ** (age_hours / half_life)

Below the floor, the auto engine holds.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone


@dataclass(frozen=True)
class Freshness:
    age_hours: float
    value: float
    stale: bool


def freshness(
    created_at: datetime,
    now: datetime | None = None,
    *,
    half_life_hours: float = 6.0,
    floor: float = 0.35,
) -> Freshness:
    now = now or datetime.now(timezone.utc)
    if created_at.tzinfo is None:
        created_at = created_at.replace(tzinfo=timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    age = max(0.0, (now - created_at).total_seconds() / 3600.0)
    hl = max(half_life_hours, 0.25)
    value = 0.5 ** (age / hl)
    return Freshness(age_hours=age, value=value, stale=value < floor)


def apply_freshness(confidence: float, fresh: Freshness) -> float:
    return confidence * fresh.value
