"""Binance-listed coins the engine may pick. Paper first."""

from __future__ import annotations

# symbol, class, why  — prices from Binance public API
BINANCE_UNIVERSE: tuple[tuple[str, str, str], ...] = (
    ("BTCUSDT", "crypto", "Binance spot"),
    ("ETHUSDT", "crypto", "Binance spot"),
    ("SOLUSDT", "crypto", "Binance spot"),
    ("BNBUSDT", "crypto", "Binance spot"),
    ("XRPUSDT", "crypto", "Binance spot"),
    ("DOGEUSDT", "crypto", "Binance spot"),
    ("ADAUSDT", "crypto", "Binance spot"),
    ("AVAXUSDT", "crypto", "Binance spot"),
    ("LINKUSDT", "crypto", "Binance spot"),
    ("DOTUSDT", "crypto", "Binance spot"),
    ("BTCUSDT.F", "crypto_futures", "Binance USDT-M perpetual"),
    ("ETHUSDT.F", "crypto_futures", "Binance USDT-M perpetual"),
    ("SOLUSDT.F", "crypto_futures", "Binance USDT-M perpetual"),
    ("BTCUSDT.C", "crypto_options", "Binance-style call — buy only"),
    ("ETHUSDT.C", "crypto_options", "Binance-style call — buy only"),
)
