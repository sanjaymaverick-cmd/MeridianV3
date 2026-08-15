"""Decorate positions with avg buy, current price, and P&L for the desk."""

from __future__ import annotations

import json

from sqlalchemy import select
from sqlalchemy.orm import Session

from meridian_v3.charges.indian import levy, normalize_broker
from meridian_v3.storage.repair import align_prices
from meridian_v3.storage.schema import Fill, Order, Position, PriceCache


def decorate_positions(session: Session, rows: list[Position]) -> list[dict]:
    out: list[dict] = []
    for row in rows:
        last = _last(session, row.symbol)
        quality = _quality(session, row.symbol)
        qty = row.qty if row.status == "open" else (row.close_qty or 0.0)
        if row.status == "closed" and qty <= 0:
            qty = _closed_qty(session, row)
        avg = row.avg_price
        fees = _fees_for(session, row)
        if row.status == "open":
            mark = last if last else avg
            avg, mark, _shifted = align_prices(avg, mark, row.market)
            gross, net = _split(row.side, avg, mark, row.qty, fees)
            out.append(_card(row, qty, avg, mark, net, "open", fees, gross=gross, price_quality=quality))
            continue
        exit_px = row.exit_price
        if exit_px is None:
            exit_px, _ignored = _infer_exit(session, row, avg, qty)
        avg, exit_px, _shifted = align_prices(avg or 0.0, exit_px or 0.0, row.market)
        gross, net = _split(row.side, avg, exit_px, qty, fees)
        out.append(
            _card(
                row,
                qty,
                avg,
                exit_px,
                net,
                "closed",
                fees,
                reason=_exit_reason(session, row),
                gross=gross,
                price_quality=quality,
            )
        )
    return out


def summarize_closed(cards: list[dict]) -> dict:
    pnls = [float(c["pnl"]) for c in cards if c.get("pnl") is not None]
    gross = [float(c["gross"]) for c in cards if c.get("gross") is not None]
    return {
        "count": len(cards),
        "wins": sum(1 for p in pnls if p > 0.5),
        "losses": sum(1 for p in pnls if p < -0.5),
        "pnl": round(sum(pnls), 2),
        "gross": round(sum(gross), 2),
        "fees": round(sum(float(c.get("fees") or 0) for c in cards), 2),
    }


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
                "correction_note": fill.correction_note,
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


MARKET_LABELS = {
    "equity_cash": "Cash",
    "options_buy": "Opt",
    "india_futures": "India F",
    "global_commodities": "Cmdty",
    "crypto_spot": "Crypto",
    "crypto_futures": "Crypto F",
    "crypto_options": "Crypto C",
    "forex_micro": "FX",
}


def _card(
    row: Position,
    qty: float,
    avg: float,
    mark: float | None,
    pnl: float | None,
    kind: str,
    fees: float,
    reason: str = "",
    gross: float | None = None,
    price_quality: str | None = None,
) -> dict:
    # 2.A.4 — a settled clip whose tape (gross) barely moved is a fee-only
    # scratch, not a directional loss. `pnl` (net of fees) is still shown as
    # a small negative number, but the color class must not read "down" the
    # same way a real loss does — that's exactly the "silently wrong" gap
    # the audit flagged. Reuse `_tone()`'s existing ₹0.50 epsilon so "flat"
    # means the same thing everywhere on the book. Open clips keep the
    # ordinary tone logic (a gross≈0 open position is just "flat right now,"
    # not a scratch outcome yet).
    pnl_class = _tone(pnl)
    if kind == "closed" and gross is not None and abs(gross) <= 0.5:
        pnl_class = "scratch"
    return {
        "id": row.id,
        "venue": row.venue,
        "symbol": row.symbol,
        "side": row.side,
        "qty": qty,
        "avg_buy": avg,
        "current": mark,
        "gross": gross,
        "gross_class": _tone(gross),
        "pnl": pnl,
        "pnl_class": pnl_class,
        "fees": fees,
        "market": row.market,
        "market_label": MARKET_LABELS.get(row.market or "", row.market or ""),
        "horizon": row.horizon,
        "status": row.status,
        # 2.A.3 — surface the flags that actually exist in the live DB so
        # the template can badge them instead of presenting every number
        # with the same visual weight (F3/F12/F15 made honest; this makes
        # that honesty visible).
        "unreconciled": row.status == "unreconciled",
        "price_quality": price_quality,
        "kind": kind,
        "reason": reason,
        "opened_at": row.opened_at,
        "closed_at": row.closed_at,
    }


def _split(side: str, avg: float | None, mark: float | None, qty: float, fees: float) -> tuple[float, float]:
    """Tape P&L (price only) and net after the contract-note bill."""
    if not avg or not mark or not qty:
        gross = 0.0
    else:
        gross = _pnl(side, avg, mark, qty)
    return gross, gross - float(fees or 0.0)


def _exit_reason(session: Session, row: Position) -> str:
    if row.status != "closed":
        return ""
    exit_side = "sell" if row.side == "buy" else "buy"
    fill = session.scalar(
        select(Fill)
        .where(Fill.symbol == row.symbol, Fill.venue == row.venue, Fill.side == exit_side)
        .order_by(Fill.filled_at.desc())
    )
    note = (fill.note if fill else "") or ""
    if "EXIT:" in note:
        body = note.split("EXIT:", 1)[1]
        return body.split("  P&L")[0].split(" P&L")[0].strip()
    return ""


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


_FLAGGABLE_QUALITY = {"fx_fallback"}


def _quality(session: Session, symbol: str) -> str | None:
    """A flaggable `PriceCache.quality` for this symbol, or None when clean.

    2.A.3 — the only quality flag currently written is "fx_fallback" (a mark
    converted through the hardcoded USDINR 83.5 fallback rather than a live
    cross-rate). "live"/"seed"/"intraday"/"missing" are ordinary states, not
    something to badge — checking "!= ok" here would be wrong, since a
    refreshed symbol's quality is "live", never the literal string "ok"
    (that's only the raw schema default before any refresh has ever run).
    """
    cache = session.scalar(select(PriceCache).where(PriceCache.symbol == symbol))
    if cache and cache.quality in _FLAGGABLE_QUALITY:
        return cache.quality
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
