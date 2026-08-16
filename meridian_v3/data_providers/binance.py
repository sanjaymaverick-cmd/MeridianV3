"""Public Binance klines. No API key. Spot and USDT-M futures."""

from __future__ import annotations

from datetime import datetime, timezone

SPOT = "https://api.binance.com/api/v3/klines"
FUTURES = "https://fapi.binance.com/fapi/v1/klines"
EXCHANGE_INFO = "https://api.binance.com/api/v3/exchangeInfo"
TICKER_24H = "https://api.binance.com/api/v3/ticker/24hr"

# A seed set so routing still works with no network (tests, offline boot).
# The live set is whatever Binance currently lists — see `spot_roots()`.
SEED_SPOT_ROOTS = {
    "BTCUSDT",
    "ETHUSDT",
    "SOLUSDT",
    "BNBUSDT",
    "XRPUSDT",
    "DOGEUSDT",
    "ADAUSDT",
    "AVAXUSDT",
    "LINKUSDT",
    "DOTUSDT",
}

# Populated on first `spot_roots()` call and reused for the process lifetime.
# Binance's listing set changes on the order of days, so a per-process cache
# is plenty fresh and keeps every price refresh from re-downloading it.
_spot_roots_cache: set[str] | None = None


def spot_roots(*, refresh: bool = False) -> set[str]:
    """Every USDT spot pair Binance currently lists as TRADING.

    This used to be a hardcoded set of ten. Anything outside it was not
    recognised as a Binance symbol, so it fell through to the yfinance
    batch — which has no quote for e.g. "PLUMEUSDT" — and silently came
    back as a missing mark. Any attempt to widen the crypto universe by
    editing the universe list alone would have failed that way.
    """
    global _spot_roots_cache
    if _spot_roots_cache is not None and not refresh:
        return _spot_roots_cache
    try:
        import httpx

        res = httpx.get(EXCHANGE_INFO, timeout=30.0)
        res.raise_for_status()
        listed = {
            s["symbol"]
            for s in res.json().get("symbols", [])
            if s.get("status") == "TRADING"
            and s.get("isSpotTradingAllowed")
            and s.get("quoteAsset") == "USDT"
        }
        _spot_roots_cache = listed | SEED_SPOT_ROOTS if listed else set(SEED_SPOT_ROOTS)
    except Exception:
        # Offline / rate-limited: fall back to the seed set rather than
        # returning empty, which would unroute every crypto symbol at once.
        _spot_roots_cache = set(SEED_SPOT_ROOTS)
    return _spot_roots_cache


def binance_pair(symbol: str) -> str:
    return symbol.split(".", 1)[0].upper()


def is_binance_symbol(symbol: str) -> bool:
    return binance_pair(symbol) in spot_roots()


def liquid_usdt_pairs(
    min_quote_volume: float = 5_000_000.0,
    limit: int | None = None,
    min_daily_range_pct: float = 0.0015,
) -> list[tuple[str, float]]:
    """TRADING USDT spot pairs with at least ``min_quote_volume`` of 24h
    turnover, richest first, as ``(symbol, quote_volume)``.

    Liquidity is the filter that matters here: Binance lists ~490 USDT
    pairs, and the thin end of that tail trades a few thousand dollars a
    day. Slippage on a name like that is a bigger cost than any edge the
    signal engine claims, so the universe is cut by turnover rather than
    taking everything Binance happens to list.
    """
    try:
        import httpx

        listed = spot_roots()
        res = httpx.get(TICKER_24H, timeout=60.0)
        res.raise_for_status()
        rows = []
        for t in res.json():
            if t.get("symbol") not in listed:
                continue
            try:
                volume = float(t.get("quoteVolume", 0.0))
                high = float(t.get("highPrice", 0.0))
                low = float(t.get("lowPrice", 0.0))
            except (TypeError, ValueError):
                continue
            # Daily range as a fraction of price. Stablecoins are the reason
            # this exists: USDCUSDT and RLUSDUSDT are among the highest-volume
            # pairs Binance lists, so a turnover filter alone waves them
            # straight through — but they move ~0.014% a day against a ~7.6%
            # hurdle, so they cannot clear costs even in principle. The desk
            # opened ₹35,735 of them (36% of the book) before this existed.
            #
            # The default sits in a real gap in the data rather than being a
            # round guess: measured across liquid pairs, the pegged assets
            # (USDC, USD1, U, RLUSD, EURI) span 0.009-0.061%, then there is a
            # 6x jump to the quietest genuine coin (BTC at 0.363%). 0.15%
            # separates them with headroom on both sides. Deliberately not
            # higher — on a calm day BTC/ETH/XRP range under 1%, and cutting
            # those would throw out the majors along with the pegs.
            day_range = (high - low) / low if low > 0 else 0.0
            rows.append((t["symbol"], volume, day_range))
    except Exception:
        return []
    rows = [r for r in rows if r[1] >= min_quote_volume and r[2] >= min_daily_range_pct]
    rows.sort(key=lambda r: r[1], reverse=True)
    trimmed = rows[:limit] if limit else rows
    return [(symbol, volume) for symbol, volume, _range in trimmed]


def fetch_klines(symbol: str, *, futures: bool = False, limit: int = 180) -> list[dict]:
    import httpx

    pair = binance_pair(symbol)
    url = FUTURES if futures else SPOT
    try:
        res = httpx.get(url, params={"symbol": pair, "interval": "1d", "limit": limit}, timeout=20.0)
        res.raise_for_status()
        raw = res.json()
    except Exception:
        return []
    if not isinstance(raw, list):
        return []
    rows: list[dict] = []
    for item in raw:
        try:
            ts = datetime.fromtimestamp(int(item[0]) / 1000, tz=timezone.utc).date()
            rows.append(
                {
                    "time": ts,
                    "Open": float(item[1]),
                    "High": float(item[2]),
                    "Low": float(item[3]),
                    "Close": float(item[4]),
                    "Volume": float(item[5]),
                }
            )
        except (TypeError, ValueError, IndexError):
            continue
    return rows
