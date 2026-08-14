"""In-process paper broker. Always available. Never talks to a real venue."""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from meridian_v3.execution.brokers.base import BrokerAdapter, OrderRequest, OrderResult, PositionSnap


class PaperBroker(BrokerAdapter):
    name = "paper"

    def __init__(self, cash: float = 50_000.0) -> None:
        self.cash = cash
        self._positions: dict[str, PositionSnap] = {}
        self._orders: dict[str, OrderResult] = {}

    def place(self, order: OrderRequest) -> OrderResult:
        px = float(order.price or 0.0)
        notional = abs(order.qty) * px
        if order.side == "buy" and notional > self.cash + 1e-9:
            return OrderResult(
                False, "", "rejected", 0.0, 0.0,
                "Paper book does not have enough cash.",
                datetime.now(timezone.utc),
            )
        bid = f"PAPER-{uuid4().hex[:10]}"
        if order.side == "buy":
            self.cash -= notional
            prev = self._positions.get(order.symbol)
            if prev:
                qty = prev.qty + order.qty
                avg = (prev.avg_price * prev.qty + px * order.qty) / qty if qty else 0.0
                self._positions[order.symbol] = PositionSnap(order.symbol, qty, avg, order.market)
            else:
                self._positions[order.symbol] = PositionSnap(order.symbol, order.qty, px, order.market)
        else:
            prev = self._positions.get(order.symbol)
            qty = (prev.qty if prev else 0.0) - order.qty
            self.cash += notional
            if abs(qty) < 1e-9:
                self._positions.pop(order.symbol, None)
            elif prev:
                self._positions[order.symbol] = PositionSnap(order.symbol, qty, prev.avg_price, order.market)
        result = OrderResult(True, bid, "filled", order.qty, px, "Paper fill.", datetime.now(timezone.utc))
        self._orders[bid] = result
        return result

    def cancel(self, broker_id: str) -> OrderResult:
        return OrderResult(False, broker_id, "rejected", 0, 0, "Paper fills are immediate.", datetime.now(timezone.utc))

    def positions(self) -> list[PositionSnap]:
        return list(self._positions.values())

    def charge(self, amount: float) -> None:
        self.cash -= max(0.0, amount)

    def funds(self) -> float:
        return self.cash
