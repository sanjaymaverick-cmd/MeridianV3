"""Per-market trading clocks.

India cash / F&O: 09:15–15:30 IST Monday–Friday. Dark from Friday 15:30
IST until Monday 09:15 IST.

Crypto (Binance): 24/7, including Saturday.

FX spot: Sunday 17:00 America/New_York through Friday 17:00. Saturday is
dark. This covers Friday after the NSE close until the US Friday close,
then Sunday evening until India reopens.

Global commodities (CME Globex / ICE): Sunday 18:00 ET through Friday
17:00 ET, with a daily 17:00–18:00 ET halt Monday–Thursday.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

from meridian_v3.config import Settings

IST = ZoneInfo("Asia/Kolkata")
ET = ZoneInfo("America/New_York")

INDIA_MARKETS = frozenset({"equity_cash", "options_buy", "india_futures"})

INDIA_WEEKEND_FLATTEN = (
    "Indian market is shut until Monday 09:15 IST. "
    "Closing this paper clip so the cash can work on crypto, FX, and global commodities."
)


def is_india_market(market: str | None) -> bool:
    return (market or "equity_cash") in INDIA_MARKETS
CRYPTO_MARKETS = frozenset({"crypto_spot", "crypto_futures", "crypto_options"})
FX_MARKETS = frozenset({"forex_micro"})
COMMODITY_MARKETS = frozenset({"global_commodities"})

# Large stand-in so "minutes to close" never trips flatten on 24/7 tape.
ALWAYS_ON_MINUTES = 10_080


@dataclass(frozen=True)
class SessionInfo:
    market: str
    in_session: bool
    minutes_to_close: int
    always_on: bool
    venue_label: str
    note: str


def _aware(now: datetime, tz: ZoneInfo) -> datetime:
    if now.tzinfo is None:
        return now.replace(tzinfo=ZoneInfo("UTC")).astimezone(tz)
    return now.astimezone(tz)


def _parse_hhmm(value: str) -> time:
    hour, minute = value.split(":")
    return time(int(hour), int(minute))


def _minutes_until(local: datetime, when: datetime) -> int:
    return int((when - local).total_seconds() // 60)


def india_session(now: datetime, settings: Settings) -> SessionInfo:
    local = _aware(now, ZoneInfo(settings.safety.timezone))
    open_t = _parse_hhmm(settings.safety.session_open)
    close_t = _parse_hhmm(settings.safety.session_close)
    current = local.time()
    weekday = local.weekday()
    in_session = open_t <= current <= close_t and weekday < 5
    close_dt = local.replace(hour=close_t.hour, minute=close_t.minute, second=0, microsecond=0)
    if in_session:
        minutes = max(0, _minutes_until(local, close_dt))
        note = "NSE/BSE cash is open."
    elif weekday < 5 and current < open_t:
        minutes = 0
        note = "Indian cash has not opened yet today."
    elif weekday == 4 and current > close_t:
        minutes = 0
        note = "Indian cash is shut until Monday 09:15 IST. Crypto, FX, and global commodities can still trade."
    elif weekday >= 5:
        minutes = 0
        note = "Indian cash is shut for the weekend (Friday 15:30 IST → Monday 09:15 IST)."
    else:
        minutes = 0
        note = "Indian cash is shut. Intraday India clips do not stay overnight."
    return SessionInfo(
        market="equity_cash",
        in_session=in_session,
        minutes_to_close=minutes,
        always_on=False,
        venue_label="NSE/BSE",
        note=note,
    )


def crypto_session(now: datetime, market: str = "crypto_spot") -> SessionInfo:
    return SessionInfo(
        market=market,
        in_session=True,
        minutes_to_close=ALWAYS_ON_MINUTES,
        always_on=True,
        venue_label="Binance",
        note="Crypto is 24/7, including Saturday and the Indian weekend.",
    )


def _fx_open(et: datetime) -> bool:
    wd = et.weekday()
    t = et.time()
    if wd == 5:
        return False
    if wd == 6:
        return t >= time(17, 0)
    if wd == 4:
        return t < time(17, 0)
    return True


def _fx_close_at(et: datetime) -> datetime:
    """Next Friday 17:00 ET from this instant (or today if Friday before 17:00)."""
    wd = et.weekday()
    days_to_fri = (4 - wd) % 7
    close = (et + timedelta(days=days_to_fri)).replace(hour=17, minute=0, second=0, microsecond=0)
    if wd == 4 and et.time() >= time(17, 0):
        close = close + timedelta(days=7)
    if wd == 5:
        close = (et + timedelta(days=6)).replace(hour=17, minute=0, second=0, microsecond=0)
    if wd == 6:
        close = (et + timedelta(days=5)).replace(hour=17, minute=0, second=0, microsecond=0)
    return close


def fx_session(now: datetime, market: str = "forex_micro") -> SessionInfo:
    et = _aware(now, ET)
    open_now = _fx_open(et)
    if open_now:
        minutes = max(0, _minutes_until(et, _fx_close_at(et)))
        note = "FX is open (Sunday 17:00 ET → Friday 17:00 ET)."
    elif et.weekday() == 5 or (et.weekday() == 6 and et.time() < time(17, 0)):
        minutes = 0
        note = "FX is shut for the weekend. Crypto is still open."
    else:
        minutes = 0
        note = "FX is shut. Next open Sunday 17:00 ET."
    return SessionInfo(
        market=market,
        in_session=open_now,
        minutes_to_close=minutes,
        always_on=False,
        venue_label="FX",
        note=note,
    )


def _commodity_open(et: datetime) -> bool:
    wd = et.weekday()
    t = et.time()
    halt = time(17, 0) <= t < time(18, 0)
    if wd == 5:
        return False
    if wd == 6:
        return t >= time(18, 0)
    if wd == 4:
        return t < time(17, 0)
    return not halt


def _commodity_close_at(et: datetime) -> datetime:
    """Next halt or Friday 17:00 ET."""
    t = et.time()
    wd = et.weekday()
    today_halt = et.replace(hour=17, minute=0, second=0, microsecond=0)
    if wd < 4 and t < time(17, 0):
        return today_halt
    if wd == 4 and t < time(17, 0):
        return today_halt
    if wd < 4 and t >= time(18, 0):
        return (et + timedelta(days=1)).replace(hour=17, minute=0, second=0, microsecond=0)
    # after Friday close, Saturday, or Sunday before 18:00 → Friday next week
    days = (4 - wd) % 7
    close = (et + timedelta(days=days)).replace(hour=17, minute=0, second=0, microsecond=0)
    if wd == 4 and t >= time(17, 0):
        close = close + timedelta(days=7)
    if wd >= 5:
        close = (et + timedelta(days=(4 - wd + 7))).replace(hour=17, minute=0, second=0, microsecond=0)
    return close


def commodity_session(now: datetime, market: str = "global_commodities") -> SessionInfo:
    et = _aware(now, ET)
    open_now = _commodity_open(et)
    if open_now:
        minutes = max(0, _minutes_until(et, _commodity_close_at(et)))
        note = "Global commodities are open (CME/ICE Sunday 18:00 ET → Friday 17:00 ET)."
    elif et.weekday() == 5 or (et.weekday() == 6 and et.time() < time(18, 0)):
        minutes = 0
        note = "Global commodities are shut for the weekend. Crypto is still open."
    else:
        minutes = 0
        note = "Global commodities are in the daily halt or shut. Next Globex open 18:00 ET."
    return SessionInfo(
        market=market,
        in_session=open_now,
        minutes_to_close=minutes,
        always_on=False,
        venue_label="COMEX/NYMEX/ICE",
        note=note,
    )


def market_session(now: datetime, settings: Settings, market: str) -> SessionInfo:
    if market in CRYPTO_MARKETS or market.startswith("crypto"):
        return crypto_session(now, market)
    if market in FX_MARKETS:
        return fx_session(now, market)
    if market in COMMODITY_MARKETS:
        return commodity_session(now, market)
    return india_session(now, settings)


def coverage_note(now: datetime, settings: Settings) -> str:
    """One line for the autopilot / desk: which tapes are live right now."""
    india = india_session(now, settings)
    fx = fx_session(now)
    cmdty = commodity_session(now)
    if india.in_session:
        return "India cash is open. Crypto 24/7. FX and global commodities follow their own clocks."
    bits = ["India cash is shut (Friday 15:30 IST → Monday 09:15 IST)", "crypto 24/7"]
    if cmdty.in_session:
        bits.append("global commodities open")
    else:
        bits.append("global commodities shut")
    if fx.in_session:
        bits.append("FX open")
    else:
        bits.append("FX shut")
    return ". ".join(bits) + "."
