"""Multi-market router.

The instrument class decides the venue. Equity cash is still home for
plain shares while NSE/BSE is open. When India is dark (Friday 15:30 IST
to Monday 09:15 IST), capital stays on tapes that are actually open:
crypto 24/7, FX 24/5, and global commodities (CME/ICE).
"""

from __future__ import annotations

from dataclasses import dataclass

from meridian_v3.universe.global_markets import is_fx_symbol, is_global_commodity

CLASS_TO_MARKET = {
    "equity": "equity_cash",
    "crypto": "crypto_spot",
    "crypto_futures": "crypto_futures",
    "crypto_options": "crypto_options",
    "future": "india_futures",
    "option": "options_buy",
    "fx": "forex_micro",
    "commodity": "global_commodities",
    "index": "options_buy",
}

# Indian listed gold / commodity proxies stay on the cash book.
_INDIA_COMMODITY_PROXIES = frozenset({"GOLD", "SILVER", "CRUDEOIL", "NATURALGAS"})


@dataclass(frozen=True)
class Route:
    market: str
    score: float
    reason: str


def market_for(asset_class: str, symbol: str = "") -> str:
    name = (symbol or "").upper()
    if name.endswith(".F") and asset_class.startswith("crypto"):
        return "crypto_futures"
    if name.endswith(".C") and asset_class.startswith("crypto"):
        return "crypto_options"
    if name.endswith(".X") or is_global_commodity(name):
        return "global_commodities"
    if name.endswith(".F"):
        return "india_futures"
    if name.endswith(".C"):
        return "options_buy"
    if name.endswith("USDT"):
        return "crypto_spot"
    if is_fx_symbol(name) or asset_class == "fx":
        return "forex_micro"
    if asset_class == "commodity" and name in _INDIA_COMMODITY_PROXIES:
        return "equity_cash"
    return CLASS_TO_MARKET.get(asset_class, "equity_cash")


def route_market(*, preferred: str | None = None) -> Route:
    """Pick the market for a symbol.

    Routing is pure suffix/asset-class dispatch (``market_for``, above) —
    every live caller (``pipeline.run_cycle``) always resolves a
    ``preferred_market`` before calling in, since ``market_for`` never
    returns ``None``. This function used to also rank equity/options/
    forex/crypto/futures/commodity scores against each other and only fall
    through to that comparison when ``preferred`` was empty, but the live
    cycle never left it empty, so that comparison never ran (F6) — it has
    been removed rather than kept on as dead code. ``preferred=None`` is a
    defensive default (equity cash) for a caller that genuinely has none.
    """
    if preferred:
        return Route(preferred, 100.0, f"This name lives on {preferred.replace('_', ' ')}.")
    return Route("equity_cash", 100.0, "No preferred market was given. Equity cash is home.")
