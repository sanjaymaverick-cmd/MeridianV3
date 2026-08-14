"""Safety Systems — drawdown pause, daily live caps, per-market sessions.

Open positions are never force-flattened by the pause rule. Only new
live risk is blocked.

Session flatten uses each market's own clock:
  * India cash / F&O — 09:15–15:30 IST weekdays
  * Crypto — never (24/7)
  * FX — Sunday 17:00 ET → Friday 17:00 ET
  * Global commodities — CME/ICE Sunday 18:00 ET → Friday 17:00 ET
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from meridian_v3.config import Settings
from meridian_v3.engine.drawdown import DrawdownState
from meridian_v3.router.calendar import india_session, market_session


@dataclass(frozen=True)
class SafetyVerdict:
    allow_paper: bool
    allow_live: bool
    reasons: tuple[str, ...]


def session_state(now: datetime, settings: Settings) -> tuple[bool, int]:
    """India cash session. Kept for callers that mean 'is NSE open?'."""
    clock = india_session(now, settings)
    return clock.in_session, clock.minutes_to_close


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

    clock = market_session(now, settings, market)
    crypto = market.startswith("crypto")
    near_own_close = (
        clock.in_session
        and clock.minutes_to_close <= settings.safety.flatten_before_close_minutes
        and not clock.always_on
    )
    own_session_closed = (not clock.in_session) and (not clock.always_on)

    session_blocks_new = False
    if crypto and settings.safety.crypto_always_on:
        session_blocks_new = False
    elif market in {"options_buy"} and settings.safety.overnight_options_forbidden:
        session_blocks_new = own_session_closed or near_own_close
    elif market == "forex_micro" and settings.safety.overnight_fx_forbidden:
        # Own FX weekend / Friday 17:00 ET — not the NSE 15:30 IST close.
        session_blocks_new = own_session_closed or near_own_close
    elif market == "global_commodities":
        session_blocks_new = (own_session_closed or near_own_close) and not settings.safety.overnight_commodities_ok
        if settings.safety.overnight_commodities_ok:
            session_blocks_new = own_session_closed or (near_own_close and horizon == "intraday")
    elif market == "india_futures" or horizon == "intraday":
        session_blocks_new = own_session_closed or near_own_close

    if session_blocks_new and horizon != "positional":
        allow_live = False
        if crypto:
            reasons.append("Crypto session block (unexpected). Paper can still learn.")
        elif market == "forex_micro":
            reasons.append(
                "FX is shut or too close to the Friday 17:00 ET close. "
                "Crypto can keep going. Paper can still learn."
            )
        elif market == "global_commodities":
            reasons.append(
                "Global commodities are shut or near the Globex halt. "
                "Crypto can keep going. Paper can still learn."
            )
        else:
            reasons.append(
                "Too close to the NSE close, or the Indian market is shut "
                "(Friday 15:30 IST → Monday 09:15 IST). "
                "Intraday India clips do not stay overnight. "
                "Crypto stays 24/7. FX and global commodities follow their own clocks."
            )

    if market in {"options_buy", "crypto_options"}:
        reasons.append("Options: buying only. Selling premium is never allowed.")

    if not reasons:
        reasons.append("Safety lights are green.")
    return SafetyVerdict(allow_paper=allow_paper, allow_live=allow_live, reasons=tuple(reasons))
