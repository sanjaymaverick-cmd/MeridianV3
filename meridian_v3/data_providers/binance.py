"""Public Binance klines. No API key. Spot and USDT-M futures."""

from __future__ import annotations

from datetime import datetime, timezone

SPOT = "https://api.binance.com/api/v3/klines"
FUTURES = "https://fapi.binance.com/fapi/v1/klines"

SPOT_ROOTS = {
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


def binance_pair(symbol: str) -> str:
    return symbol.split(".", 1)[0].upper()


def is_binance_symbol(symbol: str) -> bool:
    return binance_pair(symbol) in SPOT_ROOTS


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
