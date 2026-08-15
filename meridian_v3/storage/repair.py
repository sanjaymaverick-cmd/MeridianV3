"""One-time book repairs. Safe to run on every boot — no-ops when clean."""

from __future__ import annotations

from loguru import logger
from sqlalchemy import select
from sqlalchemy.orm import Session

from meridian_v3.alerts.notify import emit_alert
from meridian_v3.capital.sizer import stop_price
from meridian_v3.config import get_settings
from meridian_v3.storage.schema import AccountState, Fill, Position, PriceCache

# Part 3 item 6 — a repair fixing 1-3 rows on a normal boot isn't
# alert-worthy; a repair fixing dozens is worth flagging to the operator.
_LARGE_REPAIR_ALERT_THRESHOLD = 3


def repair_margin_priced_clips(session: Session) -> int:
    """Rewrite clips whose avg buy is a slice of the rupee mark.

    The OMS used to set fill = size.notional / qty. Leveraged plans store
    margin in notional, so GOLD.X showed ₹42,364 vs ₹4,23,642 and every
    silver short "stopped out" a minute later.
    """
    fixed = repair_shifted_clips(session)
    # 2.6 — a repair that actually touched rows is worth a log line; a
    # clean boot (fixed == 0, the common case) stays quiet.
    if fixed:
        logger.info("repair_margin_priced_clips: fixed {} row(s)", fixed)
    if fixed > _LARGE_REPAIR_ALERT_THRESHOLD:
        emit_alert(session, "large_repair_run", f"Repair changed {fixed} row(s) — check the book.")
    return fixed


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
    settings = get_settings()
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
        pos.stop = stop_price(pos.side, new_avg, float(pos.stop or 0.0), settings)
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
    if fixed and paper is not None:
        _reconcile_contaminated_peak(session, paper, settings)
    return fixed


def _reconcile_contaminated_peak(session: Session, paper: AccountState, settings) -> None:
    """A mispriced fill can inflate equity before repair ever sees it, and
    the drawdown peak is a one-way ratchet (``mark_to_market`` never lowers
    it) — so a since-corrected bad fill can leave the account permanently
    measuring drawdown against a peak that was never real. (This is exactly
    what happened once already: a decimal-slide fill briefly showed
    ₹1,03,373 of equity before repair caught it, and the peak stayed there
    forever after even though the position underneath it was fixed.)

    Only runs when this repair pass actually changed something (``fixed``
    in the caller) — a clean boot never second-guesses a peak nothing here
    touched. Recomputes honest equity first (so the check compares against
    today's real cash+positions, not a stale ``AccountState.equity``), then
    caps the peak down to ``max(starting capital, honest equity)`` if it's
    sitting above that. Never claims a more specific historical peak than
    that — reconstructing what equity truly was at every past moment with
    corrected prices would need replaying the whole fill history, which is
    out of scope for a boot-time repair.
    """
    from meridian_v3.pipeline import mark_to_market  # lazy: avoid a module cycle

    honest_equity = mark_to_market(session, "paper")
    honest_peak = max(settings.account.starting_equity_inr, honest_equity)
    if paper.peak > honest_peak + 0.01:
        old_peak = paper.peak
        paper.peak = honest_peak
        emit_alert(
            session,
            "peak_corrected",
            f"Paper account peak corrected from ₹{old_peak:,.2f} to ₹{honest_peak:,.2f} after "
            f"repair fixed mispriced data — the old peak was never honestly earned.",
        )


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
    settings = get_settings()
    n = 0
    rows = list(
        session.scalars(select(Position).where(Position.venue == "paper", Position.status == "open"))
    )
    for pos in rows:
        if not pos.stop or not pos.avg_price:
            continue
        line = stop_price(pos.side, float(pos.avg_price), float(pos.stop), settings)
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
    """Correct shifted fills without touching the original ``note`` (2.4).

    ``note`` is the ticket exactly as written the moment the fill happened —
    the fill journal is append-only, so it stays untouched here even when
    the repair is correcting it. What changed goes to ``correction_note``
    instead, so a reviewer can see both what was originally recorded and
    what the repair fixed, rather than a note silently rewritten in place.
    """
    fills = list(
        session.scalars(
            select(Fill).where(Fill.symbol == pos.symbol, Fill.venue == pos.venue)
        )
    )
    for fill in fills:
        old = float(fill.price or 0.0)
        if fill.side == pos.side and old and _looks_shifted(old, mark, scale):
            fill.price = old * scale
            fill.correction_note = _append_correction(
                fill.correction_note,
                f"Repair: price corrected from ₹{old:,.2f} to ₹{fill.price:,.2f} "
                f"(x{scale:g} decimal-slide fix).",
            )
        if new_pnl is not None and fill.side != pos.side:
            fill.correction_note = _append_correction(
                fill.correction_note,
                f"Repair: realized P&L corrected to ₹{new_pnl:,.0f}.",
            )


def _append_correction(existing: str, message: str) -> str:
    existing = existing or ""
    return f"{existing} {message}".strip()
