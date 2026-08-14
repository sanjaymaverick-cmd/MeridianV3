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


def route_market(
    *,
    equity_score: float,
    options_score: float,
    forex_score: float,
    crypto_score: float = 0.0,
    futures_score: float = 0.0,
    commodity_score: float = 0.0,
    options_allowed: bool = True,
    forex_allowed: bool = True,
    crypto_allowed: bool = True,
    futures_allowed: bool = True,
    commodity_allowed: bool = True,
    home_bonus: float = 8.0,
    shift_margin: float = 12.0,
    preferred: str | None = None,
) -> Route:
    if preferred:
        return Route(preferred, 100.0, f"This name lives on {preferred.replace('_', ' ')}.")
    equity = equity_score + home_bonus
    options = options_score if options_allowed else -1e9
    forex = forex_score if forex_allowed else -1e9
    crypto = crypto_score if crypto_allowed else -1e9
    futures = futures_score if futures_allowed else -1e9
    commodity = commodity_score if commodity_allowed else -1e9
    ranked = [
        ("equity_cash", equity, "Equity cash is home. We stay here unless another tape is clearly stronger."),
        ("options_buy", options, "Options buying only. Premium must fit the ₹50,000 book."),
        ("india_futures", futures, "India futures use a paper mini-lot so the ₹50,000 book can hold them."),
        (
            "global_commodities",
            commodity,
            "Global commodities (COMEX / NYMEX / ICE). Paper mini-lot, marked in rupees.",
        ),
        ("crypto_spot", crypto, "Binance crypto spot. Sized in coin crumbs, marked in rupees. 24/7."),
        ("forex_micro", forex, "Forex nano/micro only. A visit, not a new home. Open while India sleeps."),
    ]
    ranked.sort(key=lambda item: item[1], reverse=True)
    best_name, best_score, best_why = ranked[0]
    home_score = equity
    if best_name != "equity_cash" and best_score < home_score + shift_margin:
        return Route("equity_cash", home_score, "The visitor is not stronger enough. Capital stays in equity cash.")
    if best_name == "equity_cash":
        return Route(best_name, best_score, best_why)
    return Route(
        best_name,
        best_score,
        f"{best_why} Shift is allowed because this tape beats home by {best_score - home_score:.0f} points.",
    )
