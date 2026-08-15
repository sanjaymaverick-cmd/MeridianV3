"""Dry-run broker adapter — exercises the *live* order path without ever
touching a real venue.

Meridian's live order path is: arm -> live=True decision -> OMS -> ``PluginBroker``
-> whatever real adapter was registered with ``register_broker()``. Today nothing
ever calls ``register_broker()``, so the live path (as opposed to the paper path,
which is exercised constantly) has never actually been run end to end.

``DryRunBroker`` closes that gap. It implements the full ``BrokerAdapter``
contract exactly like a real venue adapter would — ``place()`` returns a
synthetic-but-well-formed ``OrderResult`` and updates in-memory
positions/cash, so ``PluginBroker`` and the OMS behave identically to how
they would against a real broker. The only difference from a real adapter is
that every ``place()`` call logs, in plain words, exactly what *would* have
been sent to a venue, and no money — real or otherwise — ever leaves the
process.

IMPORTANT — this adapter is for testing the live order path only. It is
never auto-registered at app/CLI startup (see ``meridian_v3/app.py`` and
``meridian_v3/cli.py``): a user must explicitly opt in. There are two ways:

- ``meridian-v3 register-dry-run-broker`` — a one-shot smoke test in its own
  process (registers, places one synthetic demo order, exits). This does
  **not** reach a separately-launched ``serve`` process: each CLI invocation
  is its own OS process with its own empty broker registry, so registering
  here and exiting cannot affect a desk that's already running or started
  afterward.
- ``MERIDIAN_V3_DRY_RUN_BROKER=1`` set before launching ``serve`` — this is
  the actual way to exercise the live order path against a running desk,
  since ``create_app()`` checks the env var and registers in the *same*
  process that then serves (see ``app.py:create_app()``).

Treating this as a real venue, or wiring it into normal startup unconditionally,
would silently make ``live_armed=True`` place synthetic-but-live-shaped orders
without the user ever having chosen a real broker.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from loguru import logger

from meridian_v3.execution.brokers.base import (
    BrokerAdapter,
    OrderRequest,
    OrderResult,
    PositionSnap,
)


class DryRunBroker(BrokerAdapter):
    """Logs what it would have sent to a real broker; never sends anything.

    Bookkeeping (cash/positions) mirrors ``PaperBroker`` so the rest of the
    OMS pipeline (Order/Fill/Position rows) behaves the same as it would
    against a real fill. Distinct ``name`` from ``"paper"`` so
    ``get_live_broker()`` picks it up as the registered "live" adapter once
    explicitly registered.
    """

    name = "dry_run"

    def __init__(self, cash: float = 50_000.0) -> None:
        self.cash = cash
        self._positions: dict[str, PositionSnap] = {}
        self._orders: dict[str, OrderResult] = {}

    def place(self, order: OrderRequest) -> OrderResult:
        px = float(order.price or 0.0)
        logger.info(
            "DRY-RUN would place: {} {} {} @ {} market={} venue={}",
            order.side,
            order.qty,
            order.symbol,
            px,
            order.market,
            order.venue,
        )
        notional = abs(order.qty) * px
        flatten = bool(order.extra.get("flatten"))
        if order.side == "buy" and not flatten and notional > self.cash + 1e-9:
            logger.info("DRY-RUN would reject: insufficient synthetic cash for {}", order.symbol)
            return OrderResult(
                False, "", "rejected", 0.0, 0.0,
                "DRY-RUN book does not have enough synthetic cash.",
                datetime.now(UTC),
            )
        bid = f"DRYRUN-{uuid4().hex[:10]}"
        if order.side == "buy":
            self.cash -= notional
            prev = self._positions.get(order.symbol)
            if prev:
                qty = prev.qty + order.qty
                avg = (prev.avg_price * prev.qty + px * order.qty) / qty if qty else 0.0
                self._positions[order.symbol] = PositionSnap(order.symbol, qty, avg, order.market)
            else:
                self._positions[order.symbol] = PositionSnap(
                    order.symbol, order.qty, px, order.market
                )
        else:
            prev = self._positions.get(order.symbol)
            qty = (prev.qty if prev else 0.0) - order.qty
            self.cash += notional
            if abs(qty) < 1e-9:
                self._positions.pop(order.symbol, None)
            elif prev:
                self._positions[order.symbol] = PositionSnap(
                    order.symbol, qty, prev.avg_price, order.market
                )
        result = OrderResult(
            True, bid, "filled", order.qty, px,
            "DRY-RUN fill — nothing was sent to a real venue.",
            datetime.now(UTC),
        )
        self._orders[bid] = result
        return result

    def cancel(self, broker_id: str) -> OrderResult:
        logger.info("DRY-RUN would cancel: {}", broker_id)
        return OrderResult(
            False, broker_id, "rejected", 0, 0,
            "DRY-RUN fills are immediate; nothing to cancel.",
            datetime.now(UTC),
        )

    def positions(self) -> list[PositionSnap]:
        return list(self._positions.values())

    def funds(self) -> float:
        return self.cash

    def health(self) -> str:
        return "ok (dry-run — not a real venue)"
