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


def _tickers_for(symbol: str) -> list[str]:
    if symbol == "USDINR":
        return ["USDINR=X", "INR=X"]
    if symbol == "NIFTY":
        return ["^NSEI", "^NSEBANK"]
    if symbol == "GOLD":
        return ["GOLDBEES.NS", "GC=F", "GOLD.NS"]
    parsed = normalize_symbol(symbol)
    return yahoo_candidates(parsed.symbol, parsed.exchange, parsed.yahoo)


def _history(yf, ticker: str):
    quiet = StringIO()
    logging.getLogger("yfinance").setLevel(logging.CRITICAL)
    with redirect_stdout(quiet), redirect_stderr(quiet):
        return yf.Ticker(ticker).history(period="6mo", auto_adjust=False)


class PriceProvider:
    def __init__(self, session: Session) -> None:
        self.session = session
        self.settings = get_settings()

    def refresh(self, *, force: bool = False) -> dict:
        names = list(self.session.scalars(select(WatchItem).where(WatchItem.status == "active")))
        marked = 0
        failed: list[str] = []
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
        for item in names:
            hist = None
            for ticker in _tickers_for(item.symbol):
                try:
                    hist = _history(yf, ticker)
                except Exception:
                    hist = None
                if hist is not None and not hist.empty:
                    break
            if hist is None or hist.empty:
                cache = self.session.scalar(select(PriceCache).where(PriceCache.symbol == item.symbol))
                if cache is None:
                    cache = PriceCache(symbol=item.symbol)
                    self.session.add(cache)
                cache.quality = "missing"
                cache.as_of = now
                failed.append(item.symbol)
                continue
            self.session.query(PriceBar).filter(PriceBar.symbol == item.symbol).delete()
            for idx, row in hist.iterrows():
                self.session.add(
                    PriceBar(
                        symbol=item.symbol,
                        bar_date=idx.date() if hasattr(idx, "date") else idx,
                        open=float(row["Open"]),
                        high=float(row["High"]),
                        low=float(row["Low"]),
                        close=float(row["Close"]),
                        volume=float(row.get("Volume") or 0),
                    )
                )
            closes = [float(x) for x in hist["Close"].tolist()]
            highs = [float(x) for x in hist["High"].tolist()]
            lows = [float(x) for x in hist["Low"].tolist()]
            cache = self.session.scalar(select(PriceCache).where(PriceCache.symbol == item.symbol))
            if cache is None:
                cache = PriceCache(symbol=item.symbol)
                self.session.add(cache)
            cache.last = closes[-1]
            cache.prev_close = closes[-2] if len(closes) > 1 else closes[-1]
            cache.sma20 = sum(closes[-20:]) / min(20, len(closes))
            cache.sma50 = sum(closes[-50:]) / min(50, len(closes))
            cache.high20 = max(highs[-20:])
            cache.low20 = min(lows[-20:])
            cache.volume = float(hist["Volume"].iloc[-1]) if "Volume" in hist else 0
            cache.prev_volume = float(hist["Volume"].iloc[-2]) if len(hist) > 1 else cache.volume
            cache.atr = average_true_range(highs, lows, closes, 14)
            cache.as_of = now
            cache.quality = "live"
            marked += 1
        self.session.flush()
        return {"marked": marked, "failed": len(failed), "failed_symbols": failed, "applied": marked}
