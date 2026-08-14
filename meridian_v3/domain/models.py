from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from enum import Enum


class Side(str, Enum):
    BUY = "buy"
    SELL = "sell"
    HOLD = "hold"


class Market(str, Enum):
    EQUITY_CASH = "equity_cash"
    OPTIONS_BUY = "options_buy"
    FOREX_MICRO = "forex_micro"


class Venue(str, Enum):
    PAPER = "paper"
    LIVE = "live"


class Horizon(str, Enum):
    INTRADAY = "intraday"
    POSITIONAL = "positional"


class RegimeLabel(str, Enum):
    CALM = "Calm"
    ELEVATED = "Elevated"
    STRESS = "Stress"


class TapeRegime(str, Enum):
    TRENDING = "trending"
    MEAN_REVERTING = "mean_reverting"
    HIGH_VOL = "high_vol"
    LOW_VOL = "low_vol"


class DecisionAction(str, Enum):
    BUY = "buy"
    SELL = "sell"
    HOLD = "hold"


@dataclass
class MappedRow:
    symbol: str
    exchange: str
    quantity: Decimal
    avg_cost: Decimal
    company_name: str | None = None
    isin: str | None = None
    last_price: Decimal | None = None
    sector: str | None = None
    instrument: str = "equity"
    raw: dict = field(default_factory=dict)
    error: str | None = None


@dataclass
class ImportPreview:
    broker: str
    filename: str
    headers: list[str]
    mapping: dict[str, str]
    rows: list[MappedRow]
    accepted: int
    rejected: int
    source_kind: str = "tabular"
    notes: str = ""
