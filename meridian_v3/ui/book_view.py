"""Decorate positions with avg buy, current price, and P&L for the desk."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from meridian_v3.storage.schema import Fill, Position, PriceCache


def decorate_positions(session: Session, rows: list[Position]) -> list[dict]:
    out: list[dict] = []
    for row in rows:
        last = _last(session, row.symbol)
        qty = row.qty if row.status == "open" else (row.close_qty or 0.0)
        if row.status == "closed" and qty <= 0:
            qty = _closed_qty(session, row)
        avg = row.avg_price
        if row.status == "open":
            mark = last if last else avg
            pnl = _pnl(row.side, avg, mark, row.qty)
            out.append(_card(row, qty, avg, mark, pnl, "open"))
            continue
        exit_px = row.exit_price
        pnl = row.realized_pnl
        if exit_px is None or pnl is None:
            exit_px, pnl = _infer_exit(session, row, avg, qty)
        out.append(_card(row, qty, avg, exit_px, pnl, "closed"))
    return out


def _card(row: Position, qty: float, avg: float, mark: float | None, pnl: float | None, kind: str) -> dict:
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
