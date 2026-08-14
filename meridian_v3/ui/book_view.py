"""Decorate positions with avg buy, current price, and P&L for the desk."""

from __future__ import annotations

import json

from sqlalchemy import select
from sqlalchemy.orm import Session

from meridian_v3.charges.indian import levy, normalize_broker
from meridian_v3.storage.schema import Fill, Order, Position, PriceCache


def decorate_positions(session: Session, rows: list[Position]) -> list[dict]:
    out: list[dict] = []
    for row in rows:
        last = _last(session, row.symbol)
        qty = row.qty if row.status == "open" else (row.close_qty or 0.0)
        if row.status == "closed" and qty <= 0:
            qty = _closed_qty(session, row)
        avg = row.avg_price
        fees = _fees_for(session, row)
        if row.status == "open":
            mark = last if last else avg
            pnl = _pnl(row.side, avg, mark, row.qty) - fees
            out.append(_card(row, qty, avg, mark, pnl, "open", fees))
            continue
        exit_px = row.exit_price
        pnl = row.realized_pnl
        if exit_px is None or pnl is None:
            exit_px, pnl = _infer_exit(session, row, avg, qty)
        if pnl is not None:
            # realized_pnl already nets the exit fee; still subtract leftover entry fees if stored gross
            pass
        out.append(_card(row, qty, avg, exit_px, pnl, "closed", fees))
    return out


def decorate_fills(rows: list[Fill]) -> list[dict]:
    out = []
    for fill in rows:
        raw = _charges(fill)
        out.append(
            {
                "filled_at": fill.filled_at,
                "venue": fill.venue,
                "symbol": fill.symbol,
                "side": fill.side,
                "qty": fill.qty,
                "price": fill.price,
                "note": fill.note,
                "broker": raw.get("broker", ""),
                "brokerage": float(raw.get("brokerage") or 0),
                "gst": float(raw.get("gst") or 0),
                "stt": float(raw.get("stt") or 0),
                "stamp": float(raw.get("stamp") or 0),
                "exchange": float(raw.get("exchange") or 0),
                "sebi": float(raw.get("sebi") or 0),
                "tds": float(raw.get("tds") or 0),
                "fees": float(fill.fees or raw.get("total") or 0),
            }
        )
    return out


def summarize_charges(rows: list[Fill]) -> dict:
    items = decorate_fills(rows)
    keys = ("brokerage", "gst", "stt", "stamp", "exchange", "sebi", "tds", "fees")
    totals = {key: round(sum(item[key] for item in items), 2) for key in keys}
    totals["count"] = len(items)
    return totals


def ensure_fill_charges(session: Session, broker: str) -> int:
    """Write an estimated contract-note bill onto fills that never got one."""
    name = normalize_broker(broker)
    written = 0
    for fill in session.scalars(select(Fill)):
        raw = _charges(fill)
        if raw.get("total") is not None:
            if not fill.fees:
                fill.fees = float(raw["total"])
            continue
        market = "equity_cash"
        if fill.order_id:
            order = session.get(Order, fill.order_id)
            if order and order.market:
                market = order.market
        bill = levy(
            broker=name,
            market=market,
            side=fill.side,
            qty=fill.qty,
            price=fill.price,
            product="CNC",
        )
        fill.charges_json = json.dumps(bill.as_dict())
        fill.fees = bill.total
        written += 1
    return written


def _charges(fill: Fill) -> dict:
    try:
        raw = json.loads(fill.charges_json or "{}")
    except json.JSONDecodeError:
        return {}
    return raw if isinstance(raw, dict) else {}


def _fees_for(session: Session, row: Position) -> float:
    q = select(Fill).where(Fill.symbol == row.symbol, Fill.venue == row.venue)
    if row.opened_at is not None:
        q = q.where(Fill.filled_at >= row.opened_at)
    if row.closed_at is not None:
        q = q.where(Fill.filled_at <= row.closed_at)
    return sum(float(f.fees or 0) for f in session.scalars(q))


def _card(row: Position, qty: float, avg: float, mark: float | None, pnl: float | None, kind: str, fees: float) -> dict:
    return {
        "id": row.id,
        "venue": row.venue,
        "symbol": row.symbol,
        "side": row.side,
        "qty": qty,
        "avg_buy": avg,
        "current": mark,
        "pnl": pnl,
        "pnl_class": _tone(pnl),
        "fees": fees,
        "market": row.market,
        "horizon": row.horizon,
        "status": row.status,
        "kind": kind,
        "opened_at": row.opened_at,
        "closed_at": row.closed_at,
    }


def _pnl(side: str, avg: float, mark: float, qty: float) -> float:
    raw = (mark - avg) * qty
    return -raw if side == "sell" else raw


def _tone(pnl: float | None) -> str:
    if pnl is None:
        return ""
    if pnl > 0.5:
        return "up"
    if pnl < -0.5:
        return "down"
    return ""


def _last(session: Session, symbol: str) -> float | None:
    cache = session.scalar(select(PriceCache).where(PriceCache.symbol == symbol))
    if cache and cache.last:
        return float(cache.last)
    return None


def _closed_qty(session: Session, row: Position) -> float:
    fills = list(
        session.scalars(
            select(Fill).where(Fill.symbol == row.symbol, Fill.venue == row.venue).order_by(Fill.filled_at.asc())
        )
    )
    return max((fill.qty for fill in fills), default=0.0)


def _infer_exit(session: Session, row: Position, avg: float, qty: float) -> tuple[float | None, float | None]:
    exit_side = "sell" if row.side == "buy" else "buy"
    fill = session.scalar(
        select(Fill)
        .where(Fill.symbol == row.symbol, Fill.venue == row.venue, Fill.side == exit_side)
        .order_by(Fill.filled_at.desc())
    )
    if fill is None:
        return None, None
    return fill.price, _pnl(row.side, avg, fill.price, qty or fill.qty)
