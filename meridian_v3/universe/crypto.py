"""Binance-listed coins the engine may pick. Paper first.

The static tuple below is a floor, not the whole universe: `expand_binance_universe`
pulls whatever Binance currently lists and keeps the liquid end of it.
"""

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


def expand_binance_universe(
    *, min_quote_volume: float = 5_000_000.0, limit: int | None = None
) -> tuple[tuple[str, str, str], ...]:
    """The live Binance USDT spot universe, cut by 24h turnover.

    Binance lists ~490 USDT spot pairs. Taking all of them is a bad trade
    on two counts: the thin end of that tail turns over a few thousand
    dollars a day (slippage there dwarfs any modelled edge), and every pair
    costs a klines round trip on each price refresh. Ranking by turnover
    and cutting at ``min_quote_volume`` keeps the names that can actually
    absorb a clip.

    Falls back to the static ``BINANCE_UNIVERSE`` when Binance is
    unreachable, so an offline boot still has a working crypto sleeve.
    """
    from meridian_v3.data_providers.binance import liquid_usdt_pairs

    pairs = liquid_usdt_pairs(min_quote_volume=min_quote_volume, limit=limit)
    if not pairs:
        return BINANCE_UNIVERSE

    spot = {row[0] for row in BINANCE_UNIVERSE if row[1] == "crypto"}
    out: list[tuple[str, str, str]] = list(BINANCE_UNIVERSE)
    for symbol, volume in pairs:
        if symbol in spot:
            continue
        out.append((symbol, "crypto", f"Binance spot · 24h ${volume:,.0f}"))
    return tuple(out)
