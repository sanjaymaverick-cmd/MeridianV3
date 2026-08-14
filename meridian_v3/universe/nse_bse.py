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
    ("GOLD", "NSE", "commodity", "Gold proxy"),
    ("USDINR", "NSE", "fx", "Rupee"),
)

ALGO_NOTE = "Algo universe"


def universe_symbols() -> list[str]:
    seen: list[str] = []
    for symbol, _ex, _klass, _why in ALGO_UNIVERSE:
        if symbol not in seen:
            seen.append(symbol)
    return seen


def install_universe(session: Session) -> int:
    """Put the scan list on the desk. The engine picks; the user does not have to."""
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    written = 0
    for symbol, exchange, klass, why in ALGO_UNIVERSE:
        # One row per symbol. NSE wins if both exist.
        have = session.scalar(select(WatchItem).where(WatchItem.symbol == symbol))
        note = f"{ALGO_NOTE} · {exchange} · {why}"
        if have is None:
            session.add(
                WatchItem(
                    symbol=symbol,
                    asset_class=klass,
                    status="active",
                    notes=note,
                    created_at=now,
                    updated_at=now,
                )
            )
            written += 1
        else:
            if have.status != "active":
                have.status = "active"
                written += 1
            if ALGO_NOTE not in (have.notes or ""):
                have.notes = note
            have.updated_at = now
    session.flush()
    return written
