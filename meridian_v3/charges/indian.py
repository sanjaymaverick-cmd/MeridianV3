"""Indian exchange + GST + broker charges on every fill.

Rates follow public 2024–26 discount-broker sheets (Zerodha, Angel One,
ICICI Direct). Statutory lines (STT, stamp, SEBI, exchange, GST) are
the same for every broker. Only the brokerage line changes.

These are estimates for the paper book. A live contract note can differ
by a few paise.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass

GST_RATE = 0.18
SEBI_RATE = 0.000001  # ₹10 / crore
NSE_EQ = 0.0000297
NSE_FUT = 0.0000173
NSE_OPT = 0.0003503

BROKERS = ("zerodha", "angel_one", "icici_direct")

BROKER_LABELS = {
    "zerodha": "Zerodha",
    "angel_one": "Angel One",
    "icici_direct": "ICICI Direct",
}

_ALIASES = {
    "zerodha": "zerodha",
    "kite": "zerodha",
    "zerodhakite": "zerodha",
    "angel": "angel_one",
    "angelone": "angel_one",
    "angel_one": "angel_one",
    "icici": "icici_direct",
    "icicidirect": "icici_direct",
    "icici_direct": "icici_direct",
}


def normalize_broker(name: str | None) -> str:
    key = (name or "zerodha").strip().lower().replace(" ", "_").replace("-", "_")
    key = key.replace("__", "_")
    compact = key.replace("_", "")
    return _ALIASES.get(key) or _ALIASES.get(compact) or "zerodha"


def broker_label(name: str | None) -> str:
    return BROKER_LABELS[normalize_broker(name)]


@dataclass(frozen=True)
class ChargeBill:
    broker: str
    market: str
    side: str
    product: str
    turnover: float
    brokerage: float
    stt: float
    exchange: float
    sebi: float
    stamp: float
    gst: float
    tds: float
    total: float

    def as_dict(self) -> dict:
        return asdict(self)


def levy(
    *,
    broker: str,
    market: str,
    side: str,
    qty: float,
    price: float,
    product: str = "MIS",
    contract_size: float = 1.0,
) -> ChargeBill:
    name = normalize_broker(broker)
    kind = _kind(market, product)
    turnover = abs(qty) * abs(price) * (contract_size if kind in {"future", "opt"} and market == "india_futures" else 1.0)
    if kind == "opt" and market != "india_futures":
        turnover = abs(qty) * abs(price)
    brokerage = _brokerage(name, kind, turnover)
    stt = _stt(kind, side, turnover)
    exchange = _exchange(kind, turnover)
    sebi = 0.0 if kind in {"crypto", "fx", "commodity"} else turnover * SEBI_RATE
    stamp = _stamp(kind, side, turnover)
    gst = GST_RATE * (brokerage + exchange + sebi)
    tds = turnover * 0.01 if kind == "crypto" else 0.0
    total = round(brokerage + stt + exchange + sebi + stamp + gst + tds, 2)
    return ChargeBill(
        broker=name,
        market=market,
        side=side,
        product=product,
        turnover=round(turnover, 2),
        brokerage=round(brokerage, 2),
        stt=round(stt, 2),
        exchange=round(exchange, 2),
        sebi=round(sebi, 4),
        stamp=round(stamp, 2),
        gst=round(gst, 2),
        tds=round(tds, 2),
        total=total,
    )


def _kind(market: str, product: str) -> str:
    if market in {"crypto_spot", "crypto_futures", "crypto_options"}:
        return "crypto"
    if market == "india_futures":
        return "future"
    if market == "global_commodities":
        return "commodity"
    if market in {"options_buy", "crypto_options"}:
        return "opt"
    if market == "forex_micro":
        return "fx"
    if product.upper() == "CNC":
        return "delivery"
    return "intraday"


def _brokerage(broker: str, kind: str, turnover: float) -> float:
    if kind == "crypto":
        return turnover * 0.001
    if kind in {"fx", "commodity"}:
        return min(20.0, turnover * 0.0003)
    if broker == "icici_direct":
        if kind == "delivery":
            return turnover * 0.0055
        return min(25.0, turnover * 0.0005)
    # Zerodha and Angel One discount plans: ₹0 delivery, ₹20 / 0.03% else
    if kind == "delivery":
        return 0.0
    return min(20.0, turnover * 0.0003)


def _stt(kind: str, side: str, turnover: float) -> float:
    sell = side.lower() == "sell"
    if kind == "delivery":
        return turnover * 0.001
    if kind == "intraday":
        return (turnover * 0.00025) if sell else 0.0
    if kind == "future":
        return (turnover * 0.0002) if sell else 0.0
    if kind == "opt":
        return (turnover * 0.001) if sell else 0.0
    return 0.0


def _exchange(kind: str, turnover: float) -> float:
    if kind in {"delivery", "intraday"}:
        return turnover * NSE_EQ
    if kind == "future":
        return turnover * NSE_FUT
    if kind == "opt":
        return turnover * NSE_OPT
    if kind in {"crypto", "fx", "commodity"}:
        return 0.0
    return turnover * NSE_EQ


def _stamp(kind: str, side: str, turnover: float) -> float:
    if side.lower() != "buy":
        return 0.0
    if kind == "delivery":
        return turnover * 0.00015
    if kind == "intraday":
        return turnover * 0.00003
    if kind == "future":
        return turnover * 0.00002
    if kind == "opt":
        return turnover * 0.00003
    return 0.0
