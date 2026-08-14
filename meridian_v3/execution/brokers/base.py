"""Broker-agnostic order interface.

Any Indian or overseas broker can be plugged in later by implementing
BrokerAdapter. The rest of Meridian never imports a vendor SDK.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


@dataclass(frozen=True)
class OrderRequest:
    client_id: str
    symbol: str
    side: str
    qty: float
    price: float | None
    market: str
    venue: str
    product: str = "MIS"
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class OrderResult:
    ok: bool
    broker_id: str
    status: str
    filled_qty: float
    avg_price: float
    message: str
    ts: datetime


@dataclass(frozen=True)
class PositionSnap:
    symbol: str
    qty: float
    avg_price: float
    market: str


class BrokerAdapter(ABC):
    name: str = "abstract"

    @abstractmethod
    def place(self, order: OrderRequest) -> OrderResult: ...

    @abstractmethod
    def cancel(self, broker_id: str) -> OrderResult: ...

    @abstractmethod
    def positions(self) -> list[PositionSnap]: ...

    @abstractmethod
    def funds(self) -> float: ...

    def health(self) -> str:
        return "ok"
