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

from loguru import logger
from sqlalchemy.orm import Session

from meridian_v3.capital.sizer import stop_price
from meridian_v3.charges.indian import levy
from meridian_v3.config import get_settings
from meridian_v3.decision.engine import AutoDecision
from meridian_v3.domain.copy import live_note
from meridian_v3.execution.brokers.base import OrderRequest
from meridian_v3.execution.brokers.paper_broker import PaperBroker
from meridian_v3.execution.brokers.plugin import PluginBroker
from meridian_v3.storage.schema import AccountState, Fill, Order, Position, PriceCache


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
        settings = get_settings()
        cid = f"{venue}-{uuid4().hex[:12]}"
        now = datetime.now(timezone.utc).replace(tzinfo=None)

        # 0.2 — never divide margin by lots. No honest mark ⇒ no fill (a Hold).
        price = _fill_price(decision)
        if price is None or price <= 0:
            return self._reject(
                cid, decision, venue, price=0.0, now=now,
                message="No live mark for this symbol — refusing to fabricate a fill price. Holding.",
            )

        # 0.3 — second gate: a fill wildly off the tape is a unit-mismatch bug.
        ok, last = _fill_within_bounds(self.session, decision.symbol, price, settings)
        if not ok:
            return self._reject(
                cid, decision, venue, price=price, now=now,
                message=(
                    f"Fill price ₹{price:,.2f} is more than "
                    f"{settings.execution.max_fill_deviation_pct:.0%} from the tape "
                    f"₹{last:,.2f}. Refusing a suspicious mark."
                ),
            )

        # 2.1 — second line of defense at the broker boundary. "Options buying
        # only" and "no standard FX lots" are already enforced upstream in
        # decision/engine.py's short_ok check and capital/sizer.py, but
        # neither the OMS nor PaperBroker independently re-checks market
        # rules — a future regression in the decision layer would sail
        # straight through. Reject here too, on the same shape as the
        # 0.2/0.3 guards: an Order row with status="rejected", no Fill.
        if decision.action == "sell" and decision.market in ("options_buy", "crypto_options"):
            held = (
                self.session.query(Position)
                .filter(
                    Position.symbol == decision.symbol,
                    Position.venue == venue,
                    Position.status == "open",
                )
                .one_or_none()
            )
            if held is None or held.qty <= 1e-9:
                return self._reject(
                    cid, decision, venue, price=price, now=now,
                    message=(
                        f"{decision.market} is buying-only on this book. Refusing a sell "
                        "with no open position to close."
                    ),
                )

        if decision.market == "forex_micro":
            ceiling = settings.markets.forex_micro.standard_lot_qty
            if decision.size.qty >= ceiling:
                return self._reject(
                    cid, decision, venue, price=price, now=now,
                    message=(
                        f"forex_micro forbids standard lots. Order qty {decision.size.qty:g} "
                        f"is at or above the {ceiling:g}-lot ceiling. Refusing."
                    ),
                )

        req = OrderRequest(
            client_id=cid,
            symbol=decision.symbol,
            side=decision.action,
            qty=decision.size.qty,
            price=price,
            market=decision.market,
            venue=venue,
            product="MIS" if decision.size.horizon == "intraday" else "CNC",
        )
        result = broker.place(req)
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
            self._upsert_position(decision, result.filled_qty, result.avg_price, venue, settings)
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

    def _reject(
        self, cid: str, decision: AutoDecision, venue: str, *, price: float, now: datetime, message: str
    ) -> dict:
        """Record a refused ticket without any fill, position, or fee."""
        order = Order(
            client_id=cid,
            broker_id="",
            venue=venue,
            market=decision.market,
            symbol=decision.symbol,
            side=decision.action,
            qty=decision.size.qty,
            limit_price=price,
            status="rejected",
            message=message,
            created_at=now,
        )
        self.session.add(order)
        self.session.flush()
        # 2.6 — a rejected order (0.2/0.3/2.1 guards) is exactly the kind of
        # thing an operator wants in the log, not just a DB row nobody reads.
        logger.warning(
            "order rejected: {} {} {} {} — {}",
            venue, decision.action, decision.symbol, decision.market, message,
        )
        return {
            "ok": False,
            "status": "rejected",
            "message": message,
            "broker_id": "",
            "venue": venue,
            "fees": 0.0,
            "brokerage": 0.0,
            "gst": 0.0,
        }

    def _upsert_position(
        self, decision: AutoDecision, qty: float, price: float, venue: str, settings=None
    ) -> None:
        row = (
            self.session.query(Position)
            .filter(Position.symbol == decision.symbol, Position.venue == venue, Position.status == "open")
            .one_or_none()
        )
        if row is None:
            # 1.1 — remember the exact meta-label feature vector this clip was
            # opened on, so when it closes the online logistic can be trained
            # on the same features it was scored with (see autopilot.py).
            meta = getattr(decision, "meta", None)
            features = meta.features if meta else {}
            self.session.add(
                Position(
                    venue=venue,
                    market=decision.market,
                    symbol=decision.symbol,
                    side=decision.action,
                    qty=qty,
                    avg_price=price,
                    stop=stop_price(decision.action, price, decision.size.stop, settings),
                    horizon=decision.size.horizon,
                    status="open",
                    opened_at=datetime.now(timezone.utc).replace(tzinfo=None),
                    source="auto",
                    feature_json=json.dumps(features),
                    # 2.5 — the confidence this clip opened on, so a later
                    # re-entry cooldown check can tell a genuinely stronger
                    # signal apart from the same near-miss ranking again.
                    opened_confidence=float(getattr(decision, "confidence", 0.0) or 0.0),
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
            extra={"flatten": True},
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


def _fill_price(decision: AutoDecision) -> float | None:
    """The market mark in rupees, or ``None`` when we have no honest price.

    Leveraged size plans store cash (margin) in ``notional``. Dividing that
    by lots used to record GOLD.X at ₹42,000 when the tape was ₹4,20,000 — a
    one-place decimal slide and a fake P&L. So there is deliberately no
    ``notional / qty`` fallback: no mark means no fill, and the OMS turns
    that into a Hold instead of a mispriced ticket.
    """
    if decision.price and decision.price > 0:
        return float(decision.price)
    return None


def _fill_within_bounds(
    session: Session, symbol: str, price: float, settings
) -> tuple[bool, float | None]:
    """True when ``price`` sits within the configured band of the cached tape.

    When there is no cached mark to compare against we allow the fill (the
    ``_fill_price`` gate already refused a truly absent price); the band only
    catches a mark that exists but is a 3x/10x unit-mismatch away from it.
    """
    cache = session.query(PriceCache).filter(PriceCache.symbol == symbol).one_or_none()
    last = float(cache.last) if cache is not None and cache.last else None
    if last is None or last <= 0:
        return True, last
    deviation = abs(price - last) / last
    return deviation <= settings.execution.max_fill_deviation_pct, last


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
