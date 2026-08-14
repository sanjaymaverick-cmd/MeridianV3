"""Global commodities and FX the desk can visit when India is shut.

NSE/BSE cash stops at 15:30 IST Friday and does not reopen until Monday
09:15. Crypto is 24/7. FX and CME/ICE commodities run a Sunday–Friday
tape. All marks are converted to rupees so the ₹50,000 book stays in INR.
"""

from __future__ import annotations

# symbol, venue, class, why
COMMODITY_UNIVERSE: tuple[tuple[str, str, str, str], ...] = (
    ("GOLD.X", "COMEX", "commodity", "COMEX gold"),
    ("SILVER.X", "COMEX", "commodity", "COMEX silver"),
    ("CRUDE.X", "NYMEX", "commodity", "NYMEX WTI crude"),
    ("BRENT.X", "ICE", "commodity", "ICE Brent"),
    ("NATGAS.X", "NYMEX", "commodity", "NYMEX natural gas"),
    ("COPPER.X", "COMEX", "commodity", "COMEX copper"),
    ("WHEAT.X", "CBOT", "commodity", "CBOT wheat"),
    ("CORN.X", "CBOT", "commodity", "CBOT corn"),
)

FX_UNIVERSE: tuple[tuple[str, str, str, str], ...] = (
    ("USDINR", "FX", "fx", "Dollar-rupee"),
    ("EURUSD", "FX", "fx", "Euro-dollar"),
    ("GBPUSD", "FX", "fx", "Sterling-dollar"),
    ("USDJPY", "FX", "fx", "Dollar-yen"),
    ("USDCHF", "FX", "fx", "Dollar-swiss"),
    ("AUDUSD", "FX", "fx", "Aussie-dollar"),
    ("USDCAD", "FX", "fx", "Dollar-loonie"),
    ("EURINR", "FX", "fx", "Euro-rupee"),
    ("GBPINR", "FX", "fx", "Sterling-rupee"),
)

COMMODITY_YAHOO: dict[str, tuple[str, ...]] = {
    "GOLD.X": ("GC=F",),
    "SILVER.X": ("SI=F",),
    "CRUDE.X": ("CL=F",),
    "BRENT.X": ("BZ=F",),
    "NATGAS.X": ("NG=F",),
    "COPPER.X": ("HG=F",),
    "WHEAT.X": ("ZW=F",),
    "CORN.X": ("ZC=F",),
}

FX_YAHOO: dict[str, tuple[str, ...]] = {
    "USDINR": ("USDINR=X", "INR=X"),
    "EURUSD": ("EURUSD=X",),
    "GBPUSD": ("GBPUSD=X",),
    "USDJPY": ("USDJPY=X",),
    "USDCHF": ("USDCHF=X",),
    "AUDUSD": ("AUDUSD=X",),
    "USDCAD": ("USDCAD=X",),
    "EURINR": ("EURINR=X",),
    "GBPINR": ("GBPINR=X",),
}

_COMMODITY_SYMBOLS = {row[0] for row in COMMODITY_UNIVERSE}
_FX_SYMBOLS = {row[0] for row in FX_UNIVERSE}


def is_global_commodity(symbol: str) -> bool:
    return symbol.upper().endswith(".X") or symbol.upper() in _COMMODITY_SYMBOLS


def is_fx_symbol(symbol: str) -> bool:
    return symbol.upper() in _FX_SYMBOLS


def yahoo_tickers_for(symbol: str) -> list[str]:
    key = symbol.upper()
    if key in COMMODITY_YAHOO:
        return list(COMMODITY_YAHOO[key])
    if key in FX_YAHOO:
        return list(FX_YAHOO[key])
    return []


def fx_quote_kind(symbol: str) -> str:
    """How a Yahoo FX last becomes rupees.

    * ``inr`` — already rupees per unit (USDINR, EURINR).
    * ``usd_quote`` — dollars per base (EURUSD); multiply by USDINR.
    * ``usd_base`` — foreign units per dollar (USDJPY); invert, then × USDINR.
    """
    key = symbol.upper()
    if key.endswith("INR"):
        return "inr"
    if key.startswith("USD") and key != "USDINR":
        return "usd_base"
    if key.endswith("USD"):
        return "usd_quote"
    return "inr"
