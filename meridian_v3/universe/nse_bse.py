"""Liquid NSE + BSE names the engine may pick on its own.

This is not “type a stock and we trade it”. The scanner owns this list.
Yahoo cannot price every listed name every minute, so we scan a deep
liquid set (Nifty 50, Next 50, banks, midcaps, and a BSE sleeve).
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from meridian_v3.storage.schema import WatchItem
from meridian_v3.universe.crypto import BINANCE_UNIVERSE
from meridian_v3.universe.derivatives import INDIA_DERIV_UNIVERSE
from meridian_v3.universe.global_markets import COMMODITY_UNIVERSE, FX_UNIVERSE

# (symbol, exchange, class, why)
ALGO_UNIVERSE: tuple[tuple[str, str, str, str], ...] = (
    # Nifty 50 / large NSE
    ("RELIANCE", "NSE", "equity", "Nifty 50"),
    ("HDFCBANK", "NSE", "equity", "Nifty 50"),
    ("ICICIBANK", "NSE", "equity", "Nifty 50"),
    ("INFY", "NSE", "equity", "Nifty 50"),
    ("TCS", "NSE", "equity", "Nifty 50"),
    ("ITC", "NSE", "equity", "Nifty 50"),
    ("BHARTIARTL", "NSE", "equity", "Nifty 50"),
    ("SBIN", "NSE", "equity", "Nifty 50"),
    ("LT", "NSE", "equity", "Nifty 50"),
    ("HINDUNILVR", "NSE", "equity", "Nifty 50"),
    ("BAJFINANCE", "NSE", "equity", "Nifty 50"),
    ("KOTAKBANK", "NSE", "equity", "Nifty 50"),
    ("AXISBANK", "NSE", "equity", "Nifty 50"),
    ("HCLTECH", "NSE", "equity", "Nifty 50"),
    ("MARUTI", "NSE", "equity", "Nifty 50"),
    ("SUNPHARMA", "NSE", "equity", "Nifty 50"),
    ("M&M", "NSE", "equity", "Nifty 50"),
    ("TITAN", "NSE", "equity", "Nifty 50"),
    ("NTPC", "NSE", "equity", "Nifty 50"),
    ("ONGC", "NSE", "equity", "Nifty 50"),
    ("POWERGRID", "NSE", "equity", "Nifty 50"),
    ("ULTRACEMCO", "NSE", "equity", "Nifty 50"),
    ("TATASTEEL", "NSE", "equity", "Nifty 50"),
    ("TATAMOTORS", "NSE", "equity", "Nifty 50"),
    ("ADANIENT", "NSE", "equity", "Nifty 50"),
    ("ADANIPORTS", "NSE", "equity", "Nifty 50"),
    ("ASIANPAINT", "NSE", "equity", "Nifty 50"),
    ("BAJAJ-AUTO", "NSE", "equity", "Nifty 50"),
    ("BAJAJFINSV", "NSE", "equity", "Nifty 50"),
    ("BEL", "NSE", "equity", "Nifty 50"),
    ("BPCL", "NSE", "equity", "Nifty 50"),
    ("BRITANNIA", "NSE", "equity", "Nifty 50"),
    ("CIPLA", "NSE", "equity", "Nifty 50"),
    ("COALINDIA", "NSE", "equity", "Nifty 50"),
    ("DRREDDY", "NSE", "equity", "Nifty 50"),
    ("EICHERMOT", "NSE", "equity", "Nifty 50"),
    ("GRASIM", "NSE", "equity", "Nifty 50"),
    ("HDFCLIFE", "NSE", "equity", "Nifty 50"),
    ("HEROMOTOCO", "NSE", "equity", "Nifty 50"),
    ("HINDALCO", "NSE", "equity", "Nifty 50"),
    ("INDUSINDBK", "NSE", "equity", "Nifty 50"),
    ("JSWSTEEL", "NSE", "equity", "Nifty 50"),
    ("JIOFIN", "NSE", "equity", "Nifty 50"),
    ("NESTLEIND", "NSE", "equity", "Nifty 50"),
    ("SBILIFE", "NSE", "equity", "Nifty 50"),
    ("SHRIRAMFIN", "NSE", "equity", "Nifty 50"),
    ("TATACONSUM", "NSE", "equity", "Nifty 50"),
    ("TECHM", "NSE", "equity", "Nifty 50"),
    ("TRENT", "NSE", "equity", "Nifty 50"),
    ("WIPRO", "NSE", "equity", "Nifty 50"),
    ("APOLLOHOSP", "NSE", "equity", "Nifty 50"),
    # Next 50 / liquid mid
    ("DIVISLAB", "NSE", "equity", "Nifty Next 50"),
    ("PIDILITIND", "NSE", "equity", "Nifty Next 50"),
    ("GODREJCP", "NSE", "equity", "Nifty Next 50"),
    ("DABUR", "NSE", "equity", "Nifty Next 50"),
    ("HAVELLS", "NSE", "equity", "Nifty Next 50"),
    ("SIEMENS", "NSE", "equity", "Nifty Next 50"),
    ("ABB", "NSE", "equity", "Nifty Next 50"),
    ("HAL", "NSE", "equity", "Nifty Next 50"),
    ("BEL", "NSE", "equity", "Nifty Next 50"),
    ("BOSCHLTD", "NSE", "equity", "Nifty Next 50"),
    ("DLF", "NSE", "equity", "Nifty Next 50"),
    ("LODHA", "NSE", "equity", "Nifty Next 50"),
    ("INDHOTEL", "NSE", "equity", "Nifty Next 50"),
    ("IRCTC", "NSE", "equity", "Nifty Next 50"),
    ("PFC", "NSE", "equity", "Nifty Next 50"),
    ("RECLTD", "NSE", "equity", "Nifty Next 50"),
    ("GAIL", "NSE", "equity", "Nifty Next 50"),
    ("IOC", "NSE", "equity", "Nifty Next 50"),
    ("VEDL", "NSE", "equity", "Nifty Next 50"),
    ("JINDALSTEL", "NSE", "equity", "Nifty Next 50"),
    ("TATAPOWER", "NSE", "equity", "Nifty Next 50"),
    ("TVSMOTOR", "NSE", "equity", "Nifty Next 50"),
    ("CANBK", "NSE", "equity", "Nifty Next 50"),
    ("BANKBARODA", "NSE", "equity", "Nifty Next 50"),
    ("PNB", "NSE", "equity", "Nifty Next 50"),
    ("CHOLAFIN", "NSE", "equity", "Nifty Next 50"),
    ("ICICIGI", "NSE", "equity", "Nifty Next 50"),
    ("ICICIPRULI", "NSE", "equity", "Nifty Next 50"),
    ("LTIM", "NSE", "equity", "Nifty Next 50"),
    ("PERSISTENT", "NSE", "equity", "Midcap IT"),
    ("COFORGE", "NSE", "equity", "Midcap IT"),
    ("POLYCAB", "NSE", "equity", "Midcap"),
    ("DIXON", "NSE", "equity", "Midcap"),
    ("SOLARINDS", "NSE", "equity", "Midcap"),
    ("MAXHEALTH", "NSE", "equity", "Nifty 50"),
    ("ZOMATO", "NSE", "equity", "New age"),
    ("PAYTM", "NSE", "equity", "New age"),
    ("POLICYBZR", "NSE", "equity", "New age"),
    ("NAUKRI", "NSE", "equity", "Nifty Next 50"),
    ("VBL", "NSE", "equity", "Nifty Next 50"),
    # BSE-primary sleeve (Yahoo .BO)
    ("RELIANCE", "BSE", "equity", "BSE"),
    ("HDFCBANK", "BSE", "equity", "BSE"),
    ("SBIN", "BSE", "equity", "BSE"),
    ("TCS", "BSE", "equity", "BSE"),
    ("INFY", "BSE", "equity", "BSE"),
    ("BHARTIARTL", "BSE", "equity", "BSE"),
    ("ICICIBANK", "BSE", "equity", "BSE"),
    ("ITC", "BSE", "equity", "BSE"),
    ("LT", "BSE", "equity", "BSE"),
    ("AXISBANK", "BSE", "equity", "BSE"),
    # Cross-asset the router may visit
    ("NIFTY", "NSE", "index", "Index / options buy"),
    ("BANKNIFTY", "NSE", "index", "Bank Nifty"),
    ("SENSEX", "NSE", "index", "Sensex"),
    ("GOLD", "NSE", "commodity", "Indian gold proxy (listed)"),
    ("USDINR", "NSE", "fx", "Dollar-rupee"),
)

ALGO_NOTE = "Algo universe"


def universe_symbols() -> list[str]:
    seen: list[str] = []
    for symbol, _ex, _klass, _why in ALGO_UNIVERSE:
        if symbol not in seen:
            seen.append(symbol)
    return seen


def install_universe(session: Session) -> int:
    """Put missing scan names on the desk. Existing rows are left alone."""
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    wanted: dict[str, tuple[str, str, str]] = {}
    for symbol, exchange, klass, why in ALGO_UNIVERSE:
        wanted.setdefault(symbol, (exchange, klass, why))
    for symbol, klass, why in BINANCE_UNIVERSE:
        wanted.setdefault(symbol, ("BINANCE", klass, why))
    for symbol, klass, why in INDIA_DERIV_UNIVERSE:
        wanted.setdefault(symbol, ("NSE", klass, why))
    for symbol, venue, klass, why in COMMODITY_UNIVERSE:
        wanted.setdefault(symbol, (venue, klass, why))
    for symbol, venue, klass, why in FX_UNIVERSE:
        wanted.setdefault(symbol, (venue, klass, why))
    with session.no_autoflush:
        have = {row.symbol: row for row in session.scalars(select(WatchItem))}
    written = 0
    for symbol, (exchange, klass, why) in wanted.items():
        row = have.get(symbol)
        if row is None:
            session.add(
                WatchItem(
                    symbol=symbol,
                    asset_class=klass,
                    status="active",
                    notes=f"{ALGO_NOTE} · {exchange} · {why}",
                    created_at=now,
                    updated_at=now,
                )
            )
            written += 1
            continue
        if row.status != "active":
            row.status = "active"
            written += 1
    if written:
        session.flush()
    return written
