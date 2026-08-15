from __future__ import annotations

import logging
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime, timezone
from io import StringIO

from loguru import logger
from sqlalchemy import select
from sqlalchemy.orm import Session

from meridian_v3.config import get_settings
from meridian_v3.domain.symbols import normalize_symbol, yahoo_candidates
from meridian_v3.engine.atr import average_true_range
from meridian_v3.data_providers.binance import fetch_klines, is_binance_symbol
from meridian_v3.storage.schema import PriceBar, PriceCache, WatchItem
from meridian_v3.universe.global_markets import (
    fx_quote_kind,
    is_fx_symbol,
    is_global_commodity,
    yahoo_tickers_for,
)



def _tickers_for(symbol: str) -> list[str]:
    mapped = yahoo_tickers_for(symbol)
    if mapped:
        return mapped
    if symbol == "NIFTY":
        return ["^NSEI"]
    if symbol == "BANKNIFTY":
        return ["^NSEBANK"]
    if symbol == "SENSEX":
        return ["^BSESN"]
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


def _intraday_frame(yf, ticker: str, settings):
    """Latest intraday bars for a Yahoo ticker, best-effort across intervals.

    The 6mo daily history still owns ATR / SMA / 20-day range context; this is
    only used to overlay a *moving* last mark so a same-day paper clip can
    change between its open and its close (0.6).
    """
    quiet = StringIO()
    logging.getLogger("yfinance").setLevel(logging.CRITICAL)
    period = settings.providers.intraday_period
    seen: list[str] = []
    for interval in (settings.providers.intraday_interval, "1m", "15m"):
        if interval in seen:
            continue
        seen.append(interval)
        try:
            with redirect_stdout(quiet), redirect_stderr(quiet):
                hist = yf.Ticker(ticker).history(period=period, interval=interval, auto_adjust=False)
        except Exception:
            hist = None
        if hist is not None and not getattr(hist, "empty", True):
            return hist
    return None


def _intraday_last_inr(session: Session, symbol: str, yf, settings) -> tuple[float | None, str | None]:
    """The most recent intraday close for ``symbol``, converted to rupees.

    Returns ``(price, quality_override)`` — same fallback-flagging contract
    as ``_to_inr`` (2.2), so an intraday overlay built on the USDINR fallback
    doesn't silently overwrite an honest "fx_fallback" flag with "intraday".
    """
    hist = _intraday_frame(yf, _primary_yahoo(symbol), settings)
    hist, quality_override = _to_inr(session, symbol, hist)
    if hist is None or getattr(hist, "empty", True) or "Close" not in hist.columns:
        return None, quality_override
    closes = [float(x) for x in hist["Close"].tolist() if x == x]
    return (closes[-1] if closes else None), quality_override


def _apply_frame(
    session: Session, symbol: str, hist, now: datetime, *, quality_override: str | None = None
) -> bool:
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
    # 2.2 — a mark built off the hardcoded USDINR fallback is not "live"; it
    # is honest-but-stale until the real USDINR fetch recovers.
    cache.quality = quality_override or "live"
    return True


class PriceProvider:
    def __init__(self, session: Session) -> None:
        self.session = session
        self.settings = get_settings()

    def refresh(self, *, force: bool = False) -> dict:
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
        index_specials = {"NIFTY", "BANKNIFTY", "SENSEX", "GOLD"}
        crypto = [item for item in names if is_binance_symbol(item.symbol)]
        global_names = [
            item
            for item in names
            if is_fx_symbol(item.symbol) or is_global_commodity(item.symbol)
        ]
        deriv = [
            item
            for item in names
            if "." in item.symbol
            and not is_binance_symbol(item.symbol)
            and not is_global_commodity(item.symbol)
        ]
        batch = [
            item
            for item in names
            if item.symbol not in index_specials
            and item not in crypto
            and item not in deriv
            and item not in global_names
        ]
        leftovers = [item for item in names if item.symbol in index_specials] + list(global_names)
        leftovers.sort(key=lambda item: 0 if item.symbol == "USDINR" else 1)
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
            hist, quality_override = _to_inr(self.session, item.symbol, hist)
            if _apply_frame(self.session, item.symbol, hist, now, quality_override=quality_override):
                marked += 1
                got.add(item.symbol)

        # Overlay a moving intraday mark on the Yahoo names before derived
        # clones copy their parent, so futures/options inherit the fresh last.
        self._overlay_intraday(yf, list(batch) + list(leftovers), now, got)

        marked += _refresh_binance(self.session, crypto, now, got)
        marked += _clone_derived(self.session, names, now, got)

        failed = [n.symbol for n in names if n.symbol not in got]
        for symbol in failed:
            cache = self.session.scalar(select(PriceCache).where(PriceCache.symbol == symbol))
            if cache is None:
                cache = PriceCache(symbol=symbol)
                self.session.add(cache)
            cache.quality = "missing"
            cache.as_of = now
        self.session.flush()
        if failed:
            # 2.6 — a symbol with no live mark this pass is a Hold, not a
            # crash, but it's still worth a log line so a bad night is
            # debuggable after the fact.
            logger.warning("price refresh: {} symbol(s) failed: {}", len(failed), ", ".join(failed))
        return {"marked": marked, "failed": len(failed), "failed_symbols": failed, "applied": marked}

    def _overlay_intraday(self, yf, items, now: datetime, got: set[str]) -> int:
        """Replace the daily close with the latest intraday mark where we can.

        Best-effort and per-symbol: any failure leaves the honest daily close
        in place. Only touches names we already marked from Yahoo this pass.
        """
        if not self.settings.providers.intraday_marks:
            return 0
        n = 0
        for item in items:
            if item.symbol not in got:
                continue
            try:
                px, quality_override = _intraday_last_inr(self.session, item.symbol, yf, self.settings)
            except Exception:
                px, quality_override = None, None
            if px is None or px <= 0:
                continue
            cache = self.session.scalar(select(PriceCache).where(PriceCache.symbol == item.symbol))
            if cache is None:
                continue
            cache.last = px
            cache.as_of = now
            cache.quality = quality_override or "intraday"
            n += 1
        return n

    def refresh_alt_markets(self) -> dict:
        """Binance + India F&O clones + FX/commodity Yahoo. No full NSE scan."""
        from datetime import datetime, timezone

        now = datetime.now(timezone.utc).replace(tzinfo=None)
        names = list(self.session.scalars(select(WatchItem).where(WatchItem.status == "active")))
        crypto = [item for item in names if is_binance_symbol(item.symbol)]
        got: set[str] = set()
        marked = _refresh_binance(self.session, crypto, now, got)
        marked += _clone_derived(self.session, names, now, got)
        try:
            import yfinance as yf
        except ImportError:
            yf = None
        if yf is not None:
            globals_ = [
                item
                for item in names
                if (is_fx_symbol(item.symbol) or is_global_commodity(item.symbol))
                and item.symbol not in got
            ]
            globals_.sort(key=lambda item: 0 if item.symbol == "USDINR" else 1)
            for item in globals_:
                hist = None
                for ticker in _tickers_for(item.symbol):
                    try:
                        hist = _history(yf, ticker)
                    except Exception:
                        hist = None
                    if hist is not None and not hist.empty:
                        break
                hist, quality_override = _to_inr(self.session, item.symbol, hist)
                if _apply_frame(self.session, item.symbol, hist, now, quality_override=quality_override):
                    marked += 1
                    got.add(item.symbol)
        self.session.flush()
        return {"marked": marked, "got": tuple(got)}


def _scale_ohlc(hist, factor: float):
    if hist is None or getattr(hist, "empty", True) or factor == 1.0:
        return hist
    out = hist.copy()
    for col in ("Open", "High", "Low", "Close"):
        if col in out.columns:
            out[col] = out[col] * factor
    return out


def _invert_ohlc_to_inr(hist, usdinr: float):
    """USDXXX quotes (JPY per dollar) → rupees per foreign unit."""
    if hist is None or getattr(hist, "empty", True):
        return hist
    out = hist.copy()
    if "Open" in out.columns:
        out["Open"] = usdinr / out["Open"]
    if "Close" in out.columns:
        out["Close"] = usdinr / out["Close"]
    if "High" in out.columns and "Low" in out.columns:
        new_high = usdinr / out["Low"]
        new_low = usdinr / out["High"]
        out["High"] = new_high
        out["Low"] = new_low
    return out


def _to_inr(session: Session, symbol: str, hist) -> tuple[object, str | None]:
    """Mark global names in rupees so the book never mixes currencies.

    Returns ``(hist, quality_override)``. ``quality_override`` is
    ``"fx_fallback"`` whenever the conversion leaned on the hardcoded 83.5
    USDINR fallback (2.2) instead of a live USDINR cache, so the caller can
    flag the resulting ``PriceCache`` row rather than mark it "live".
    """
    if hist is None or getattr(hist, "empty", True):
        return hist, None
    if is_global_commodity(symbol):
        fx, is_fallback = _usdinr(session)
        return _scale_ohlc(hist, fx), ("fx_fallback" if is_fallback else None)
    if not is_fx_symbol(symbol):
        return hist, None
    kind = fx_quote_kind(symbol)
    if kind == "inr":
        return hist, None
    fx, is_fallback = _usdinr(session)
    quality = "fx_fallback" if is_fallback else None
    if kind == "usd_quote":
        return _scale_ohlc(hist, fx), quality
    if kind == "usd_base":
        return _invert_ohlc_to_inr(hist, fx), quality
    return hist, None


def _usdinr(session: Session) -> tuple[float, bool]:
    """USDINR rupees-per-dollar, and whether it's the hardcoded fallback.

    Every commodity/FX mark built from this rate inherits the same fallback
    flag (2.2), so a missing or stale USDINR cache can never silently pass
    as a live cross-rate elsewhere on the book.
    """
    row = session.scalar(select(PriceCache).where(PriceCache.symbol == "USDINR"))
    if row and row.last and row.last > 50:
        return float(row.last), False
    return 83.5, True


def _refresh_binance(session: Session, items, now: datetime, got: set[str]) -> int:
    import pandas as pd

    fx, _is_fallback = _usdinr(session)
    marked = 0
    roots: dict[str, list] = {}
    for item in items:
        roots.setdefault(item.symbol.split(".", 1)[0], []).append(item)
    for pair, group in roots.items():
        futures = any(i.symbol.endswith(".F") or i.asset_class == "crypto_futures" for i in group)
        rows = fetch_klines(pair, futures=futures) or fetch_klines(pair, futures=False)
        if not rows:
            continue
        frame = pd.DataFrame(rows).set_index("time")
        for col in ("Open", "High", "Low", "Close"):
            frame[col] = frame[col] * fx
        if _apply_frame(session, pair, frame, now):
            got.add(pair)
            marked += 1
        spot = session.scalar(select(PriceCache).where(PriceCache.symbol == pair))
        if spot is None or not spot.last:
            continue
        for item in group:
            if item.symbol == pair:
                continue
            if item.symbol.endswith(".F") or item.asset_class == "crypto_futures":
                if _copy_cache(session, pair, item.symbol, now, scale=1.0):
                    got.add(item.symbol)
                    marked += 1
            elif item.symbol.endswith(".C") or item.asset_class == "crypto_options":
                if _copy_cache(session, pair, item.symbol, now, scale=0.03):
                    got.add(item.symbol)
                    marked += 1
    return marked


_UNDERLYING = {
    "NIFTY.F": "NIFTY",
    "NIFTY.C": "NIFTY",
    "BANKNIFTY.F": "BANKNIFTY",
    "BANKNIFTY.C": "BANKNIFTY",
    "SENSEX.F": "SENSEX",
    "RELIANCE.F": "RELIANCE",
    "RELIANCE.C": "RELIANCE",
    "HDFCBANK.F": "HDFCBANK",
    "INFY.F": "INFY",
}


def _clone_derived(session: Session, names, now: datetime, got: set[str]) -> int:
    marked = 0
    for item in names:
        under = _UNDERLYING.get(item.symbol)
        if not under:
            continue
        scale = 0.015 if item.symbol.endswith(".C") or item.asset_class == "option" else 1.0
        if _copy_cache(session, under, item.symbol, now, scale=scale):
            got.add(item.symbol)
            marked += 1
    return marked


def _copy_cache(session: Session, src: str, dest: str, now: datetime, *, scale: float) -> bool:
    source = session.scalar(select(PriceCache).where(PriceCache.symbol == src))
    if source is None or not source.last:
        return False
    cache = session.scalar(select(PriceCache).where(PriceCache.symbol == dest))
    if cache is None:
        cache = PriceCache(symbol=dest)
        session.add(cache)
    cache.last = source.last * scale
    cache.prev_close = (source.prev_close or source.last) * scale
    cache.sma20 = (source.sma20 or source.last) * scale
    cache.sma50 = (source.sma50 or source.last) * scale
    cache.high20 = (source.high20 or source.last) * scale
    cache.low20 = (source.low20 or source.last) * scale
    cache.volume = source.volume
    cache.prev_volume = source.prev_volume
    cache.atr = (source.atr or source.last * 0.015) * scale
    cache.as_of = now
    cache.quality = source.quality or "live"
    return True
