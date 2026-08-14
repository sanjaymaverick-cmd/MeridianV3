from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from meridian_v3.config import get_settings
from meridian_v3.domain.symbols import normalize_symbol
from meridian_v3.engine.atr import average_true_range
from meridian_v3.storage.schema import PriceBar, PriceCache, WatchItem


class PriceProvider:
    def __init__(self, session: Session) -> None:
        self.session = session
        self.settings = get_settings()

    def refresh(self, *, force: bool = False) -> dict:
        names = list(self.session.scalars(select(WatchItem).where(WatchItem.status == "active")))
        marked = 0
        failed = 0
        if not self.settings.providers.yfinance_enabled:
            return {"marked": 0, "failed": 0, "applied": 0, "note": "yfinance disabled"}
        try:
            import yfinance as yf
        except ImportError:
            return {"marked": 0, "failed": len(names), "applied": 0, "note": "yfinance missing"}

        now = datetime.now(timezone.utc).replace(tzinfo=None)
        for item in names:
            parsed = normalize_symbol(item.symbol)
            ticker = "USDINR=X" if item.symbol == "USDINR" else parsed.yahoo
            if item.symbol == "NIFTY":
                ticker = "^NSEI"
            try:
                hist = yf.Ticker(ticker).history(period="6mo", auto_adjust=False)
            except Exception:
                failed += 1
                continue
            if hist is None or hist.empty:
                failed += 1
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
        return {"marked": marked, "failed": failed, "applied": marked}
