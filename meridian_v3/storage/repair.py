"""One-time book repairs. Safe to run on every boot — no-ops when clean."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from meridian_v3.capital.sizer import stop_price
from meridian_v3.config import get_settings
from meridian_v3.storage.schema import AccountState, Fill, Position, PriceCache


def repair_margin_priced_clips(session: Session) -> int:
    """Rewrite clips whose avg buy is a slice of the rupee mark.

    The OMS used to set fill = size.notional / qty. Leveraged plans store
    margin in notional, so GOLD.X showed ₹42,364 vs ₹4,23,642 and every
    silver short "stopped out" a minute later.
    """
    return repair_shifted_clips(session)


def align_prices(entry: float, other: float, market: str | None = None) -> tuple[float, float, float | None]:
    """Move a one-place decimal slide so both marks sit on the rupee tape.

    SILVER.X was stored as ₹621.82 vs ₹6,218.20. The small number is the
    one that's wrong — multiply it by 10. A short that sold 6,228.70 and
    covered 6,216.29 is a few paise, not −₹224.
    """
    a = float(entry or 0.0)
    b = float(other or 0.0)
    if a <= 0 or b <= 0:
        return a, b, None
    lo, hi = (a, b) if a <= b else (b, a)
    scale = _scale_from_ratio(hi / lo, market)
    if not scale:
        return a, b, None
    if a < b:
        return a * scale, b, scale
    return a, b * scale, scale


def repair_shifted_clips(session: Session) -> int:
    paper = session.scalar(select(AccountState).where(AccountState.venue == "paper"))
    rows = list(session.scalars(select(Position).where(Position.venue == "paper")))
    fixed = 0
    for pos in rows:
        # A closed clip with no exit_price has nothing honest to align against.
        # Never rewrite avg_price against today's mark: reconstruct the exit
        # from the real matching fill, or flag the row unreconciled.
        if pos.status == "closed" and pos.exit_price is None:
            if _reconcile_closed_without_exit(session, pos):
                fixed += 1
            continue
        mark = _mark_for(session, pos)
        avg = float(pos.avg_price or 0.0)
        new_avg, new_mark, scale = align_prices(avg, mark, pos.market)
        if not scale:
            continue
        extra = (new_avg - avg) * float(pos.close_qty or pos.qty or 0.0)
        pos.avg_price = new_avg
        if pos.status == "closed" and pos.exit_price and abs(new_mark - float(pos.exit_price)) > 0.01:
            pos.exit_price = new_mark
        pos.stop = stop_price(pos.side, new_avg, float(pos.stop or 0.0))
        new_pnl = None
        if pos.status == "closed" and pos.exit_price:
            qty = float(pos.close_qty or 0.0) or _qty_from_fills(session, pos)
            raw = (float(pos.exit_price) - new_avg) * qty
            if pos.side == "sell":
                raw = -raw
            new_pnl = raw - _fees_for(session, pos)
            pos.realized_pnl = new_pnl
            if qty and not pos.close_qty:
                pos.close_qty = qty
        if paper is not None:
            if pos.side == "buy":
                paper.cash -= extra
            else:
                paper.cash += extra
        _scale_fills(session, pos, new_mark or mark, scale, new_pnl=new_pnl)
        fixed += 1
    fixed += _normalize_open_stops(session)
    return fixed


def _reconcile_closed_without_exit(session: Session, pos: Position) -> bool:
    """Give a closed-but-exit-less clip an honest exit, or flag it.

    Looks up the matching exit ``Fill`` (same approach ``book_view._infer_exit``
    uses) and reconstructs ``exit_price`` / ``realized_pnl`` / ``close_qty``
    from it. If no exit fill exists there is nothing to reconcile against, so
    the row is marked ``status="unreconciled"`` — visibly excluded from equity
    and training rather than silently rewritten against the current mark.

    Idempotent: once ``exit_price`` is set (or the row is flagged) a second
    run of :func:`repair_shifted_clips` no longer enters this branch.
    """
    exit_side = "sell" if pos.side == "buy" else "buy"
    fill = session.scalar(
        select(Fill)
        .where(Fill.symbol == pos.symbol, Fill.venue == pos.venue, Fill.side == exit_side)
        .order_by(Fill.filled_at.desc())
    )
    if fill is None:
        pos.status = "unreconciled"
        return True
    exit_price = float(fill.price or 0.0)
    if exit_price <= 0:
        pos.status = "unreconciled"
        return True
    qty = float(pos.close_qty or 0.0) or float(pos.qty or 0.0) or float(fill.qty or 0.0)
    avg = float(pos.avg_price or 0.0)
    raw = (exit_price - avg) * qty
    if pos.side == "sell":
        raw = -raw
    pos.exit_price = exit_price
    pos.realized_pnl = raw - _fees_for(session, pos)
    if not pos.close_qty and qty:
        pos.close_qty = qty
    return True


def _normalize_open_stops(session: Session) -> int:
    """Turn leftover ATR distances on open clips into price lines."""
    n = 0
    rows = list(
        session.scalars(select(Position).where(Position.venue == "paper", Position.status == "open"))
    )
    for pos in rows:
        if not pos.stop or not pos.avg_price:
            continue
        line = stop_price(pos.side, float(pos.avg_price), float(pos.stop))
        if abs(line - float(pos.stop)) > 0.01:
            pos.stop = line
            n += 1
    return n


def _scale_for(market: str) -> float | None:
    settings = get_settings()
    if market == "global_commodities":
        pct = settings.markets.global_commodities.margin_pct or 0.10
        return 1.0 / pct if pct > 0 else 10.0
    if market == "india_futures":
        spec = settings.markets.india_futures
        unit = spec.contract_size * spec.lot_step * (spec.margin_pct or 0.10)
        return 1.0 / unit if unit > 0 else None
    if market == "crypto_futures":
        return max(1.0, settings.markets.crypto_futures.max_leverage)
    return None


def _scale_from_ratio(ratio: float, market: str | None) -> float | None:
    if ratio <= 1.01:
        return None
    for scale in (10.0, 100.0):
        if 0.75 * scale <= ratio <= 1.35 * scale:
            return scale
    configured = _scale_for(market or "")
    if configured and 0.75 * configured <= ratio <= 1.35 * configured:
        return configured
    return None


def _looks_shifted(avg: float, last: float, scale: float) -> bool:
    """True when avg buy is margin (last ≈ avg × 10 for commodities)."""
    if avg <= 0 or last <= 0 or scale <= 1.01:
        return False
    ratio = last / avg
    return 0.75 * scale <= ratio <= 1.35 * scale


def _mark_for(session: Session, pos: Position) -> float:
    if pos.status == "closed" and pos.exit_price:
        return float(pos.exit_price)
    cache = session.scalar(select(PriceCache).where(PriceCache.symbol == pos.symbol))
    if cache is not None and cache.last:
        return float(cache.last)
    return 0.0


def _qty_from_fills(session: Session, pos: Position) -> float:
    fills = list(
        session.scalars(
            select(Fill).where(Fill.symbol == pos.symbol, Fill.venue == pos.venue)
        )
    )
    return max((float(fill.qty) for fill in fills), default=0.0)


def _fees_for(session: Session, pos: Position) -> float:
    q = select(Fill).where(Fill.symbol == pos.symbol, Fill.venue == pos.venue)
    if pos.opened_at is not None:
        q = q.where(Fill.filled_at >= pos.opened_at)
    if pos.closed_at is not None:
        q = q.where(Fill.filled_at <= pos.closed_at)
    return sum(float(f.fees or 0) for f in session.scalars(q))


def _scale_fills(
    session: Session,
    pos: Position,
    mark: float,
    scale: float,
    *,
    new_pnl: float | None,
) -> None:
    fills = list(
        session.scalars(
            select(Fill).where(Fill.symbol == pos.symbol, Fill.venue == pos.venue)
        )
    )
    for fill in fills:
        old = float(fill.price or 0.0)
        if fill.side == pos.side and old and _looks_shifted(old, mark, scale):
            fill.price = old * scale
            fill.note = _rewrite_price(fill.note, old, fill.price)
        if new_pnl is not None and fill.side != pos.side:
            fill.note = _rewrite_pnl(fill.note, new_pnl)


def _rewrite_price(note: str, old: float, new: float) -> str:
    if not note:
        return note
    for fmt in (f"{old:,.2f}", f"{old:.2f}"):
        if fmt in note:
            return note.replace(fmt, f"{new:,.2f}", 1)
    return note


def _rewrite_pnl(note: str, pnl: float) -> str:
    if not note or "P&L" not in note:
        return note
    head, _, tail = note.partition("P&L")
    rest = tail
    if " after" in rest:
        rest = " after" + rest.split(" after", 1)[1]
    else:
        rest = ""
    return f"{head}P&L ₹{pnl:,.0f}{rest}"
