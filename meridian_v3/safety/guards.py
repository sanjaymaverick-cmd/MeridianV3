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
    daily_loss_inr: float = 0.0,
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

    # Part 3 item 3 — kill switch: an absolute rupee-per-day circuit breaker,
    # on top of the drawdown-percentage pause above. This one blocks *both*
    # paper and live new risk (paper is exactly where "the desk is bleeding
    # fast today" should be caught early) — but, matching the drawdown-pause
    # convention, existing open positions are never force-flattened here.
    if daily_loss_inr >= settings.safety.max_daily_loss_inr:
        allow_live = False
        allow_paper = False
        reasons.append(
            f"Daily loss line hit: down ₹{daily_loss_inr:,.0f} today against the "
            f"₹{settings.safety.max_daily_loss_inr:,.0f} daily loss cap. New paper and "
            "live risk both stop for the rest of the day. Open positions stay open — "
            "nothing is force-flattened."
        )

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
        # Paper is blocked here too, not just live. A shut venue has no
        # executable price — the only mark available is the last session's
        # stale close, so a "fill" against it is a price the book could
        # never actually have gotten. That is the same dishonest-fill
        # problem the audit's F1/F2 were about, and it poisons training
        # twice over: the entry price is fiction, and the outcome it
        # teaches the belief/logit is fiction too. Observed live: a Sunday
        # COPPER.X buy of ~₹18,000 (18% of the book) filled at Friday's
        # close. Crypto is unaffected — it is genuinely 24/7, so
        # `session_blocks_new` stays False for it above.
        allow_paper = False
        if crypto:
            reasons.append("Crypto session block (unexpected).")
        elif market == "forex_micro":
            reasons.append(
                "FX is shut or too close to the Friday 17:00 ET close. "
                "No new paper either — a shut venue has no executable price. "
                "Crypto can keep going."
            )
        elif market == "global_commodities":
            reasons.append(
                "Global commodities are shut or near the Globex halt. "
                "No new paper either — a shut venue has no executable price. "
                "Crypto can keep going."
            )
        else:
            reasons.append(
                "Too close to the NSE close, or the Indian market is shut "
                "(Friday 15:30 IST → Monday 09:15 IST). "
                "Intraday India clips do not stay overnight. "
                "No new paper either — a shut venue has no executable price. "
                "Crypto stays 24/7. FX and global commodities follow their own clocks."
            )

    if market in {"options_buy", "crypto_options"}:
        reasons.append("Options: buying only. Selling premium is never allowed.")

    if not reasons:
        reasons.append("Safety lights are green.")
    return SafetyVerdict(allow_paper=allow_paper, allow_live=allow_live, reasons=tuple(reasons))
