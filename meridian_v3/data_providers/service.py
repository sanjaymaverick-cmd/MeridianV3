from __future__ import annotations

import logging
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime, timezone
from io import StringIO

from sqlalchemy import select
from sqlalchemy.orm import Session

from meridian_v3.config import get_settings
from meridian_v3.domain.symbols import normalize_symbol, yahoo_candidates
from meridian_v3.engine.atr import average_true_range
from meridian_v3.storage.schema import PriceBar, PriceCache, WatchItem
from meridian_v3.universe import install_universe


def _tickers_for(symbol: str) -> list[str]:
    if symbol == "USDINR":
        return ["USDINR=X", "INR=X"]
    if symbol == "NIFTY":
        return ["^NSEI", "^NSEBANK"]
    if symbol == "GOLD":
        return ["GOLDBEES.NS", "GC=F", "GOLD.NS"]
    parsed = normalize_symbol(symbol)
    return yahoo_candidates(parsed.symbol, parsed.exchange, parsed.yahoo)


def _primary_yahoo(symbol: str) -> str:
    return _tickers_for(symbol)[0]


def _history(yf, ticker: str):
    quiet = StringIO()
    logging.getLogger("yfinance").setLevel(logging.CRITICAL)
    with redirect_stdout(quiet), redirect_stderr(quiet):
        return yf.Ticker(ticker).history(period="6mo", auto_adjust=False)


def _apply_frame(session: Session, symbol: str, hist, now: datetime) -> bool:
    if hist is None or getattr(hist, "empty", True):
        return False
    needed = ("Open", "High", "Low", "Close")
    if any(col not in hist.columns for col in needed):
        return False
    session.query(PriceBar).filter(PriceBar.symbol == symbol).delete()
    for idx, row in hist.iterrows():
        session.add(
            PriceBar(
                symbol=symbol,
                bar_date=idx.date() if hasattr(idx, "date") else idx,
                open=float(row["Open"]),
                high=float(row["High"]),
                low=float(row["Low"]),
                close=float(row["Close"]),
                volume=float(row["Volume"]) if "Volume" in hist.columns else 0.0,
            )
        )
    closes = [float(x) for x in hist["Close"].tolist() if x == x]
    highs = [float(x) for x in hist["High"].tolist() if x == x]
    lows = [float(x) for x in hist["Low"].tolist() if x == x]
    if len(closes) < 5:
        return False
    cache = session.scalar(select(PriceCache).where(PriceCache.symbol == symbol))
    if cache is None:
        cache = PriceCache(symbol=symbol)
        session.add(cache)
    cache.last = closes[-1]
    cache.prev_close = closes[-2] if len(closes) > 1 else closes[-1]
    cache.sma20 = sum(closes[-20:]) / min(20, len(closes))
    cache.sma50 = sum(closes[-50:]) / min(50, len(closes))
    cache.high20 = max(highs[-20:])
    cache.low20 = min(lows[-20:])
    cache.volume = float(hist["Volume"].iloc[-1]) if "Volume" in hist.columns else 0
    cache.prev_volume = float(hist["Volume"].iloc[-2]) if "Volume" in hist.columns and len(hist) > 1 else cache.volume
    cache.atr = average_true_range(highs, lows, closes, 14)
    cache.as_of = now
    cache.quality = "live"
    return True


class PriceProvider:
    def __init__(self, session: Session) -> None:
        self.session = session
        self.settings = get_settings()

    def refresh(self, *, force: bool = False) -> dict:
        install_universe(self.session)
        names = list(self.session.scalars(select(WatchItem).where(WatchItem.status == "active")))
        if not names:
            return {"marked": 0, "failed": 0, "failed_symbols": [], "applied": 0, "note": "empty universe"}
        if not self.settings.providers.yfinance_enabled:
            return {"marked": 0, "failed": 0, "failed_symbols": [], "applied": 0, "note": "yfinance disabled"}
        try:
            import yfinance as yf
        except ImportError:
            return {
                "marked": 0,
                "failed": len(names),
                "failed_symbols": [n.symbol for n in names],
                "applied": 0,
                "note": "yfinance missing",
            }

        now = datetime.now(timezone.utc).replace(tzinfo=None)
        logging.getLogger("yfinance").setLevel(logging.CRITICAL)
        specials = {"USDINR", "NIFTY", "GOLD"}
        batch = [item for item in names if item.symbol not in specials]
        leftovers = [item for item in names if item.symbol in specials]
        marked = 0
        got: set[str] = set()

        yahoo_map = {item.symbol: _primary_yahoo(item.symbol) for item in batch}
        if yahoo_map:
            quiet = StringIO()
            try:
                with redirect_stdout(quiet), redirect_stderr(quiet):
                    raw = yf.download(
                        list(yahoo_map.values()),
                        period="6mo",
                        group_by="ticker",
                        auto_adjust=False,
                        threads=True,
                        progress=False,
                    )
            except Exception:
                raw = None
            if raw is not None and not raw.empty:
                for symbol, ticker in yahoo_map.items():
                    try:
                        if ticker in raw.columns.get_level_values(0):
                            frame = raw[ticker].dropna(how="all")
                        else:
                            frame = raw.dropna(how="all") if len(yahoo_map) == 1 else None
                    except Exception:
                        frame = None
                    if frame is not None and _apply_frame(self.session, symbol, frame, now):
                        marked += 1
                        got.add(symbol)

        for item in leftovers + [n for n in batch if n.symbol not in got]:
            hist = None
            for ticker in _tickers_for(item.symbol):
                try:
                    hist = _history(yf, ticker)
                except Exception:
                    hist = None
                if hist is not None and not hist.empty:
                    break
            if _apply_frame(self.session, item.symbol, hist, now):
                marked += 1
                got.add(item.symbol)

        failed = [n.symbol for n in names if n.symbol not in got]
        for symbol in failed:
            cache = self.session.scalar(select(PriceCache).where(PriceCache.symbol == symbol))
            if cache is None:
                cache = PriceCache(symbol=symbol)
                self.session.add(cache)
            cache.quality = "missing"
            cache.as_of = now
        self.session.flush()
        return {"marked": marked, "failed": len(failed), "failed_symbols": failed, "applied": marked}
