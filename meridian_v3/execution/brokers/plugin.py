"""Live broker plugin slot.

No vendor SDK is bundled. Register an adapter at runtime:

    from meridian_v3.execution.brokers import register_broker
    register_broker(MyZerodhaAdapter())

Until then, live orders are refused with a clear message.
"""

from __future__ import annotations

from datetime import datetime, timezone

from meridian_v3.execution.brokers.base import BrokerAdapter, OrderRequest, OrderResult, PositionSnap

_REGISTRY: dict[str, BrokerAdapter] = {}


def register_broker(adapter: BrokerAdapter) -> None:
    _REGISTRY[adapter.name] = adapter


def get_live_broker() -> BrokerAdapter | None:
    for name, adapter in _REGISTRY.items():
        if name != "paper":
            return adapter
    return None


class PluginBroker(BrokerAdapter):
    name = "plugin"

    def place(self, order: OrderRequest) -> OrderResult:
        live = get_live_broker()
        if live is None:
            return OrderResult(
                False,
                "",
                "rejected",
                0.0,
                0.0,
                "No live broker is plugged in. Paper still works. "
                "Add an adapter with register_broker(...) when you are ready.",
                datetime.now(timezone.utc),
            )
        return live.place(order)

    def cancel(self, broker_id: str) -> OrderResult:
        live = get_live_broker()
        if live is None:
            return OrderResult(False, broker_id, "rejected", 0, 0, "No live broker.", datetime.now(timezone.utc))
        return live.cancel(broker_id)

    def positions(self) -> list[PositionSnap]:
        live = get_live_broker()
        return live.positions() if live else []

    def funds(self) -> float:
        live = get_live_broker()
        return live.funds() if live else 0.0
