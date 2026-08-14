"""Safety Systems — drawdown pause, daily live caps, overnight filters.

Open positions are never force-flattened by the pause rule. Only new
live risk is blocked.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time
from zoneinfo import ZoneInfo

from meridian_v3.config import Settings
from meridian_v3.engine.drawdown import DrawdownState


@dataclass(frozen=True)
class SafetyVerdict:
    allow_paper: bool
    allow_live: bool
    reasons: tuple[str, ...]


def _parse_hhmm(value: str) -> time:
    hour, minute = value.split(":")
    return time(int(hour), int(minute))


def session_state(now: datetime, settings: Settings) -> tuple[bool, int]:
    """Return (in_session, minutes_to_close)."""
    tz = ZoneInfo(settings.safety.timezone)
    local = now.astimezone(tz) if now.tzinfo else now.replace(tzinfo=tz)
    open_t = _parse_hhmm(settings.safety.session_open)
    close_t = _parse_hhmm(settings.safety.session_close)
    current = local.time()
    in_session = open_t <= current <= close_t and local.weekday() < 5
    close_dt = local.replace(hour=close_t.hour, minute=close_t.minute, second=0, microsecond=0)
    minutes = int((close_dt - local).total_seconds() // 60)
    return in_session, minutes


def evaluate_safety(
    *,
    drawdown: DrawdownState,
    settings: Settings,
    live_armed: bool,
    live_today: int,
    confidence: float,
    market: str,
    horizon: str,
    now: datetime,
) -> SafetyVerdict:
    reasons: list[str] = []
    allow_paper = True
    allow_live = True

    if not live_armed:
        allow_live = False
        reasons.append("Live switch is off. Paper still runs.")

    if drawdown.live_paused:
        allow_live = False
        reasons.append(drawdown.reason)

    cap = (
        settings.safety.max_daily_live_high_conf
        if confidence >= settings.sizing.high_confidence
        else settings.safety.max_daily_live_trades
    )
    if live_today >= cap:
        allow_live = False
        reasons.append(
            f"Today already has {live_today} live clips. "
            f"The daily live line is {cap}. Paper can still learn."
        )

    in_session, minutes = session_state(now, settings)
    crypto = market.startswith("crypto")
    overnight_blocked = (not crypto) and (
        (market in {"options_buy", "crypto_options"} and settings.safety.overnight_options_forbidden)
        or (market == "forex_micro" and settings.safety.overnight_fx_forbidden)
        or (horizon == "intraday")
        or market == "india_futures"
    )
    if (not in_session or minutes <= settings.safety.flatten_before_close_minutes) and overnight_blocked:
        if horizon != "positional":
            allow_live = False
            reasons.append(
                "Too close to the close, or the Indian market is shut. "
                "Intraday India clips do not stay overnight. Crypto can keep going."
            )

    if market in {"options_buy", "crypto_options"}:
        reasons.append("Options: buying only. Selling premium is never allowed.")

    if not reasons:
        reasons.append("Safety lights are green.")
    return SafetyVerdict(allow_paper=allow_paper, allow_live=allow_live, reasons=tuple(reasons))
