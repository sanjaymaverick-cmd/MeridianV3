"""Order Management System.

Hybrid rule: every accepted decision is paper-traded. The same ticket
is cloned to live only when the Auto Decision Engine set live=True
and a broker adapter is present.

Fills write journal rows. Nothing vendor-specific lives here.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy.orm import Session

from meridian_v3.charges.indian import levy
from meridian_v3.config import get_settings
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
            bill = _bill(
                market=decision.market,
                side=decision.action,
                qty=result.filled_qty,
                price=result.avg_price,
                product=req.product,
                session=self.session,
            )
            _take_fees(broker, venue, bill.total)
            fill = Fill(
                order_id=order.id,
                venue=venue,
                symbol=decision.symbol,
                side=decision.action,
                qty=result.filled_qty,
                price=result.avg_price,
                fees=bill.total,
                charges_json=json.dumps(bill.as_dict()),
                filled_at=now,
                note=live_note(
                    symbol=decision.symbol,
                    side=decision.action,
                    qty=result.filled_qty,
                    price=result.avg_price,
                    venue=venue,
                )
                + f" Fees ₹{bill.total:,.2f} (brokerage ₹{bill.brokerage:,.2f} + GST ₹{bill.gst:,.2f}).",
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
            "fees": bill.total if result.ok else 0.0,
            "brokerage": bill.brokerage if result.ok else 0.0,
            "gst": bill.gst if result.ok else 0.0,
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
                pnl = (price - row.avg_price) * row.qty
                if row.side == "sell":
                    pnl = -pnl
                row.status = "closed"
                row.close_qty = row.qty
                row.exit_price = price
                row.realized_pnl = pnl
                row.qty = 0
                row.closed_at = datetime.now(timezone.utc).replace(tzinfo=None)
            else:
                row.qty = new_qty

    def close_position(self, pos: Position, *, price: float, reason: str) -> dict:
        """Close an open paper (or live) clip at a mark. Used by the autopilot."""
        if pos.status != "open" or pos.qty <= 0:
            return {"ok": False, "message": "already closed"}
        exit_side = "sell" if pos.side == "buy" else "buy"
        cid = f"{pos.venue}-x{uuid4().hex[:10]}"
        product = "MIS" if pos.horizon == "intraday" else "CNC"
        req = OrderRequest(
            client_id=cid,
            symbol=pos.symbol,
            side=exit_side,
            qty=pos.qty,
            price=price,
            market=pos.market,
            venue=pos.venue,
            product=product,
        )
        broker = self.paper if pos.venue == "paper" else self.live
        result = broker.place(req)
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        order = Order(
            client_id=cid,
            broker_id=result.broker_id,
            venue=pos.venue,
            market=pos.market,
            symbol=pos.symbol,
            side=exit_side,
            qty=pos.qty,
            limit_price=price,
            status=result.status,
            message=result.message,
            created_at=now,
        )
        self.session.add(order)
        self.session.flush()
        if not result.ok:
            return {"ok": False, "message": result.message}
        pnl = (price - pos.avg_price) * pos.qty
        if pos.side == "sell":
            pnl = -pnl
        bill = _bill(
            market=pos.market,
            side=exit_side,
            qty=result.filled_qty,
            price=result.avg_price,
            product=product,
            session=self.session,
        )
        _take_fees(broker, pos.venue, bill.total)
        pnl -= bill.total + _entry_fees(self.session, pos)
        fill = Fill(
            order_id=order.id,
            venue=pos.venue,
            symbol=pos.symbol,
            side=exit_side,
            qty=result.filled_qty,
            price=result.avg_price,
            fees=bill.total,
            charges_json=json.dumps(bill.as_dict()),
            filled_at=now,
            note=(
                f"{'PAPER' if pos.venue == 'paper' else 'LIVE'} EXIT: {reason}  "
                f"P&L ₹{pnl:,.0f} after fees ₹{bill.total:,.2f} "
                f"(brokerage ₹{bill.brokerage:,.2f} + GST ₹{bill.gst:,.2f})."
            ),
        )
        self.session.add(fill)
        pos.status = "closed"
        pos.close_qty = pos.qty
        pos.exit_price = result.avg_price
        pos.realized_pnl = pnl
        pos.qty = 0
        pos.closed_at = now
        self._touch_account(pos.venue, result)
        self.session.flush()
        return {"ok": True, "pnl": pnl, "reason": reason}

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


def _entry_fees(session: Session, pos: Position) -> float:
    fills = session.query(Fill).filter(Fill.symbol == pos.symbol, Fill.venue == pos.venue)
    if pos.opened_at is not None:
        fills = fills.filter(Fill.filled_at >= pos.opened_at)
    return sum(float(f.fees or 0) for f in fills)


def _bill(*, market: str, side: str, qty: float, price: float, product: str, session: Session | None = None):
    settings = get_settings()
    size = settings.markets.india_futures.contract_size if market == "india_futures" else 1.0
    broker_name = settings.account.broker
    if session is not None:
        acct = session.query(AccountState).filter(AccountState.venue == "paper").one_or_none()
        if acct is not None and getattr(acct, "broker", None):
            broker_name = acct.broker
    return levy(
        broker=broker_name,
        market=market,
        side=side,
        qty=qty,
        price=price,
        product=product,
        contract_size=size,
    )


def _take_fees(broker, venue: str, amount: float) -> None:
    if amount <= 0:
        return
    if venue == "paper" and hasattr(broker, "charge"):
        broker.charge(amount)
