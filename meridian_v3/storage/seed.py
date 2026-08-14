from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from meridian_v3.config import get_settings
from meridian_v3.storage.schema import (
    AccountState,
    EquityPoint,
    OptionLegRow,
    PriceBar,
    PriceCache,
    RegimeState,
    WatchItem,
)
from meridian_v3.universe import install_universe


DEMO_WATCH = (
    ("RELIANCE", "equity", "Home name — cash book"),
    ("HDFCBANK", "equity", "Quality compounder"),
    ("INFY", "equity", "IT beta"),
    ("TCS", "equity", "Cash-rich IT"),
    ("TATAMOTORS", "equity", "Cyclical tape"),
    ("NIFTY", "index", "Options buying only, when the signal is huge"),
    ("GOLD", "commodity", "MCX-style gold via a listed proxy — paper only"),
    ("USDINR", "fx", "Nano/micro only"),
)


def _bars(symbol: str, last: float, days: int = 80) -> list[PriceBar]:
    rows: list[PriceBar] = []
    px = last * 0.92
    start = date.today() - timedelta(days=days)
    for i in range(days):
        drift = (last - px) / max(days - i, 1)
        noise = ((i * 17) % 7 - 3) * last * 0.002
        o = px
        c = px + drift + noise
        h = max(o, c) * 1.006
        l = min(o, c) * 0.994
        rows.append(
            PriceBar(
                symbol=symbol,
                bar_date=start + timedelta(days=i),
                open=round(o, 2),
                high=round(h, 2),
                low=round(l, 2),
                close=round(c, 2),
                volume=1_000_000 + i * 2500,
            )
        )
        px = c
    return rows


def seed_demo(session: Session, *, reset: bool = False) -> int:
    """Create the demo book. Safe to click again — missing pieces are filled in.

    Does not wipe imported holdings unless reset=True.
    """
    if reset:
        for table in (
            WatchItem, PriceBar, PriceCache, AccountState, EquityPoint,
            OptionLegRow, RegimeState,
        ):
            session.query(table).delete()
        session.flush()
    return ensure_demo(session)


def ensure_demo(session: Session) -> int:
    """Add the algo NSE/BSE universe, seed tape, and the ₹50,000 paper/live books."""
    settings = get_settings()
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    written = 0
    written += install_universe(session)

    for symbol, klass, notes in DEMO_WATCH:
        have = session.scalar(select(WatchItem).where(WatchItem.symbol == symbol))
        if have is None:
            session.add(
                WatchItem(
                    symbol=symbol, asset_class=klass, status="active",
                    notes=notes, created_at=now, updated_at=now,
                )
            )
            written += 1
        elif have.status != "active":
            have.status = "active"
            have.updated_at = now
            written += 1

    marks = {
        "RELIANCE": 1384.0,
        "HDFCBANK": 1662.0,
        "INFY": 1488.0,
        "TCS": 3125.0,
        "TATAMOTORS": 678.0,
        "NIFTY": 24480.0,
        "GOLD": 7250.0,
        "USDINR": 83.55,
    }
    for symbol, last in marks.items():
        cache = session.scalar(select(PriceCache).where(PriceCache.symbol == symbol))
        bar_count = session.scalar(select(PriceBar.id).where(PriceBar.symbol == symbol).limit(1))
        if cache is not None and cache.last and bar_count is not None and cache.quality != "missing":
            continue
        if bar_count is None:
            session.add_all(_bars(symbol, last))
            session.flush()
        bars = list(session.scalars(select(PriceBar).where(PriceBar.symbol == symbol)))
        if not bars:
            bars = _bars(symbol, last)
            session.add_all(bars)
            session.flush()
        closes = [b.close for b in bars]
        highs = [b.high for b in bars]
        lows = [b.low for b in bars]
        if cache is None:
            cache = PriceCache(symbol=symbol)
            session.add(cache)
        cache.last = closes[-1]
        cache.prev_close = closes[-2] if len(closes) > 1 else closes[-1]
        cache.sma20 = sum(closes[-20:]) / min(20, len(closes))
        cache.sma50 = sum(closes[-50:]) / min(50, len(closes))
        cache.high20 = max(highs[-20:])
        cache.low20 = min(lows[-20:])
        cache.volume = bars[-1].volume
        cache.prev_volume = bars[-2].volume
        cache.atr = sum(h - l for h, l in zip(highs[-14:], lows[-14:])) / 14
        cache.as_of = now
        if not cache.quality or cache.quality == "missing":
            cache.quality = "seed"
        written += 1

    start = settings.account.starting_equity_inr
    for venue in ("paper", "live"):
        acct = session.scalar(select(AccountState).where(AccountState.venue == venue))
        if acct is None:
            session.add(
                AccountState(
                    venue=venue,
                    name=settings.account.name,
                    cash=start,
                    equity=start,
                    peak=start,
                    live_armed=0,
                    paper_auto=1,
                    broker=settings.account.broker or "zerodha",
                    updated_at=now,
                )
            )
            session.add(EquityPoint(venue=venue, as_of=now, equity=start, cash=start, peak=start))
            written += 1
            continue
        if not getattr(acct, "broker", None):
            acct.broker = settings.account.broker or "zerodha"
            written += 1
        if lift_book_to_start(acct, start, now):
            written += 1

    leg = session.scalar(select(OptionLegRow).where(OptionLegRow.leg_id == "demo-nifty-ce"))
    if leg is None:
        session.add(
            OptionLegRow(
                leg_id="demo-nifty-ce",
                symbol="NIFTY",
                contract_label="NIFTY 24400 CE",
                lots=1,
                multiplier=75,
                mark_inr=120.0,
                delta=0.42,
                gamma=0.012,
                vega_per_lot=18.0,
                theta_per_lot=-8.5,
                iv=0.13,
                greeks_as_of=now,
                stale=0,
            )
        )
        written += 1

    regime = session.scalar(select(RegimeState).order_by(RegimeState.as_of.desc()))
    if regime is None:
        session.add(
            RegimeState(
                desk="Elevated",
                tape="trending",
                vol="low_vol",
                reason="Seeded desk · NSE/BSE algo universe · starting ₹50,000 book",
                as_of=now,
            )
        )
        written += 1
    session.flush()
    return written


def lift_book_to_start(acct: AccountState, start: float, now) -> bool:
    """Credit an old ₹5,000 demo book up to ₹50,000 without wiping open paper clips."""
    if acct.peak + 1 >= start and acct.equity + 1 >= start:
        return False
    if acct.peak <= 5000.01:
        credit = start - acct.peak
        acct.cash += credit
        acct.equity += credit
        acct.peak = start
        acct.updated_at = now
        return True
    unused = abs(acct.equity - acct.cash) < 1.0
    if unused and acct.equity + 1 < start and not acct.live_armed:
        acct.cash = start
        acct.equity = start
        acct.peak = start
        acct.updated_at = now
        return True
    return False
