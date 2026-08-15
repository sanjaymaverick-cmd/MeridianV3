"""Multi-year historical backfill for backtesting — split/bonus-adjusted
NSE daily bars, primarily via yfinance.

Deliberately separate from ``service.py``'s live refresh path: this writes
into ``HistoricalBar``, never ``PriceBar`` (the live 6mo rolling window that
gets wiped and reinserted every refresh cycle). A backtest's data must stay
put regardless of what the live desk is doing in the background.

**Why yfinance, not jugaad-data, is the primary source here** (verified
empirically, not assumed): NSE's own bhavcopy — what ``jugaad-data`` scrapes
— is the raw as-traded tape, with no split/bonus adjustment at all. A stock
that does a 1:1 bonus shows a ~50% overnight "crash" in that raw data. This
was checked directly against RELIANCE's real 2024-10-28 bonus: jugaad-data's
``stock_df()`` shows the price falling from ~₹2,655 to ~₹1,334 across that
one date — an entirely artificial gap that would corrupt every momentum/
volatility feature computed across it. yfinance's ``Close`` column, even
with ``auto_adjust=False``, is already split-adjusted (that flag only
toggles *dividend* adjustment) — confirmed no gap across the same dates for
RELIANCE (2024-10-28) or HDFCBANK (2025-08-26, also a real 1:1 bonus).

jugaad-data is kept as a fallback for symbols yfinance can't return data
for at all — matching what it's actually good for (being an NSE-direct
source), without inheriting its adjustment gap for the primary path.
"""

from __future__ import annotations

import logging
import time
from contextlib import redirect_stderr, redirect_stdout
from datetime import date
from io import StringIO

from sqlalchemy import select
from sqlalchemy.orm import Session

from meridian_v3.storage.schema import HistoricalBar, WatchItem


def _fetch_yfinance(symbol: str, years: int):
    """Split/bonus-adjusted daily bars via yfinance, or None on failure."""
    import yfinance as yf

    quiet = StringIO()
    logging.getLogger("yfinance").setLevel(logging.CRITICAL)
    try:
        with redirect_stdout(quiet), redirect_stderr(quiet):
            df = yf.Ticker(f"{symbol}.NS").history(period=f"{years}y", auto_adjust=False)
    except Exception:
        return None
    if df is None or df.empty:
        return None
    return df


def _fetch_jugaad_fallback(symbol: str, years: int):
    """Raw (unadjusted) NSE bars via jugaad-data — only used when yfinance
    has nothing for this symbol at all. Never the primary path: see the
    module docstring for why (no split/bonus adjustment)."""
    from datetime import timedelta

    from jugaad_data.nse import stock_df

    to_date = date.today()
    from_date = to_date - timedelta(days=365 * years)
    try:
        df = stock_df(symbol=symbol, from_date=from_date, to_date=to_date, series="EQ")
    except Exception:
        return None
    if df is None or df.empty:
        return None
    return df


def backfill_nse_symbol(session: Session, symbol: str, *, years: int = 5) -> int:
    """Backfill/upsert ``HistoricalBar`` rows for one NSE equity symbol.

    Idempotent: an existing (symbol, bar_date) row is updated in place
    rather than duplicated, so re-running a backfill (e.g. to extend the
    window, or refresh a range) is always safe.
    """
    yf_df = _fetch_yfinance(symbol, years)
    if yf_df is not None and not yf_df.empty:
        rows = (
            (idx.date() if hasattr(idx, "date") else idx, row["Open"], row["High"], row["Low"], row["Close"], row.get("Volume", 0.0), "yfinance")
            for idx, row in yf_df.iterrows()
        )
    else:
        jg_df = _fetch_jugaad_fallback(symbol, years)
        if jg_df is None:
            return 0
        rows = (
            (
                row["DATE"].date() if hasattr(row["DATE"], "date") else row["DATE"],
                row["OPEN"], row["HIGH"], row["LOW"], row["CLOSE"], row.get("VOLUME", 0.0),
                "jugaad_nse_unadjusted",
            )
            for _, row in jg_df.iterrows()
        )

    existing = {
        row.bar_date: row
        for row in session.scalars(select(HistoricalBar).where(HistoricalBar.symbol == symbol))
    }
    written = 0
    for bar_date, o_raw, h_raw, l_raw, c_raw, v_raw, source in rows:
        try:
            o, h, l, c = float(o_raw), float(h_raw), float(l_raw), float(c_raw)
            v = float(v_raw) if v_raw == v_raw else 0.0  # NaN guard
        except (TypeError, ValueError):
            continue
        # A holiday/no-trade row can come back as all-zero — skip it rather
        # than write a fake bar that would corrupt ATR/indicator math.
        if o <= 0 or h <= 0 or l <= 0 or c <= 0:
            continue
        # A long (multi-year) fetch can come back with a duplicate row for
        # the same date (observed in practice with jugaad-data — plausibly
        # an internal pagination-boundary overlap). `existing` only reflects
        # rows already committed *before* this call, so track what this
        # loop has itself already written too, or a same-date duplicate
        # later in the same fetch would trip the unique constraint.
        bar = existing.get(bar_date)
        if bar is None:
            bar = HistoricalBar(symbol=symbol, bar_date=bar_date, open=o, high=h, low=l, close=c, volume=v, source=source)
            session.add(bar)
            existing[bar_date] = bar
        else:
            bar.open, bar.high, bar.low, bar.close, bar.volume, bar.source = o, h, l, c, v, source
        written += 1
    return written


def backfill_nse_watchlist(session: Session, *, years: int = 5, sleep_ms: int = 400) -> dict:
    """Backfill every active NSE-equity symbol on the watchlist.

    ``sleep_ms`` between symbols is polite to both providers — jugaad-data's
    own docs specifically ask callers not to hammer NSE's site.
    """
    symbols = list(
        session.scalars(
            select(WatchItem.symbol).where(WatchItem.status == "active", WatchItem.asset_class == "equity")
        )
    )
    results: dict[str, int] = {}
    failed: list[str] = []
    for i, symbol in enumerate(symbols):
        n = backfill_nse_symbol(session, symbol, years=years)
        if n:
            results[symbol] = n
        else:
            failed.append(symbol)
        session.flush()
        if i < len(symbols) - 1:
            time.sleep(sleep_ms / 1000)
    return {
        "symbols": len(symbols),
        "ok": len(results),
        "failed": failed,
        "bars_written": sum(results.values()),
    }
