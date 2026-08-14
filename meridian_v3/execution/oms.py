"""Order Management System.

Hybrid rule: every accepted decision is paper-traded. The same ticket
is cloned to live only when the Auto Decision Engine set live=True
and a broker adapter is present.

Fills write journal rows. Nothing vendor-specific lives here.
"""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy.orm import Session

from meridian_v3.decision.engine import AutoDecision
from meridian_v3.domain.copy import live_note
from meridian_v3.execution.brokers.base import OrderRequest
from meridian_v3.execution.brokers.paper_broker import PaperBroker
from meridian_v3.execution.brokers.plugin import PluginBroker
from meridian_v3.storage.schema import AccountState, Fill, Order, Position


class OrderManager:
    def __init__(self, session: Session, paper: PaperBroker, live: PluginBroker | None = None) -> None:
        self.session = session
        self.paper = paper
        self.live = live or PluginBroker()

    def execute(self, decision: AutoDecision) -> dict:
        out: dict[str, object] = {"paper": None, "live": None}
        if decision.paper:
            out["paper"] = self._send(decision, venue="paper", broker=self.paper)
        if decision.live:
            out["live"] = self._send(decision, venue="live", broker=self.live)
        return out

    def _send(self, decision: AutoDecision, *, venue: str, broker) -> dict:
        cid = f"{venue}-{uuid4().hex[:12]}"
        req = OrderRequest(
            client_id=cid,
            symbol=decision.symbol,
            side=decision.action,
            qty=decision.size.qty,
            price=decision.size.notional / decision.size.qty if decision.size.qty else None,
            market=decision.market,
            venue=venue,
            product="MIS" if decision.size.horizon == "intraday" else "CNC",
        )
        result = broker.place(req)
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        order = Order(
            client_id=cid,
            broker_id=result.broker_id,
            venue=venue,
            market=decision.market,
            symbol=decision.symbol,
            side=decision.action,
            qty=decision.size.qty,
            limit_price=req.price,
            status=result.status,
            message=result.message,
            created_at=now,
        )
        self.session.add(order)
        self.session.flush()
        if result.ok:
            fill = Fill(
                order_id=order.id,
                venue=venue,
                symbol=decision.symbol,
                side=decision.action,
                qty=result.filled_qty,
                price=result.avg_price,
                fees=0.0,
                filled_at=now,
                note=live_note(
                    symbol=decision.symbol,
                    side=decision.action,
                    qty=result.filled_qty,
                    price=result.avg_price,
                    venue=venue,
                ),
            )
            self.session.add(fill)
            self._upsert_position(decision, result.filled_qty, result.avg_price, venue)
            self._touch_account(venue, result)
        self.session.flush()
        return {
            "ok": result.ok,
            "status": result.status,
            "message": result.message,
            "broker_id": result.broker_id,
            "venue": venue,
        }

    def _upsert_position(self, decision: AutoDecision, qty: float, price: float, venue: str) -> None:
        row = (
            self.session.query(Position)
            .filter(Position.symbol == decision.symbol, Position.venue == venue, Position.status == "open")
            .one_or_none()
        )
        if row is None:
            self.session.add(
                Position(
                    venue=venue,
                    market=decision.market,
                    symbol=decision.symbol,
                    side=decision.action,
                    qty=qty,
                    avg_price=price,
                    stop=decision.size.stop,
                    horizon=decision.size.horizon,
                    status="open",
                    opened_at=datetime.now(timezone.utc).replace(tzinfo=None),
                    source="auto",
                )
            )
            return
        if row.side == decision.action:
            new_qty = row.qty + qty
            row.avg_price = (row.avg_price * row.qty + price * qty) / new_qty if new_qty else 0.0
            row.qty = new_qty
        else:
            new_qty = row.qty - qty
            if new_qty <= 1e-9:
                row.status = "closed"
                row.qty = 0
                row.closed_at = datetime.now(timezone.utc).replace(tzinfo=None)
            else:
                row.qty = new_qty

    def _touch_account(self, venue: str, result) -> None:
        acct = self.session.query(AccountState).filter(AccountState.venue == venue).one_or_none()
        if acct is None:
            return
        if result.ok:
            # cash is owned by the broker object for paper; live adapter reports funds
            if venue == "paper":
                acct.cash = self.paper.funds()
            else:
                acct.cash = self.live.funds()
            acct.updated_at = datetime.now(timezone.utc).replace(tzinfo=None)
