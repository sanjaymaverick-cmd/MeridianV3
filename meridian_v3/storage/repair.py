"""One-time book repairs. Safe to run on every boot — no-ops when clean."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from meridian_v3.config import get_settings
from meridian_v3.storage.schema import AccountState, Fill, Position, PriceCache


def _scale_for(market: str) -> float | None:
    settings = get_settings()
    if market == "global_commodities":
        pct = settings.markets.global_commodities.margin_pct or 0.10
        return 1.0 / pct if pct > 0 else 10.0
    if market == "india_futures":
        pct = settings.markets.india_futures.margin_pct or 0.10
        return 1.0 / pct if pct > 0 else 10.0
    if market == "crypto_futures":
        return max(1.0, settings.markets.crypto_futures.max_leverage)
    return None


def _looks_shifted(avg: float, last: float, scale: float) -> bool:
    """True when avg buy is margin (last ≈ avg × 10 for commodities)."""
    if avg <= 0 or last <= 0 or scale <= 1.01:
        return False
    ratio = last / avg
    return 0.75 * scale <= ratio <= 1.35 * scale


def repair_margin_priced_clips(session: Session) -> int:
    """Rewrite clips whose avg buy is 10% (or 50%) of the rupee mark.

    The OMS used to set fill = size.notional / qty, and leveraged plans
    store margin in notional. GOLD.X showed ₹42,364 vs ₹4,23,642.
    """
    paper = session.scalar(select(AccountState).where(AccountState.venue == "paper"))
    rows = list(
        session.scalars(select(Position).where(Position.status == "open", Position.venue == "paper"))
    )
    fixed = 0
    for pos in rows:
        scale = _scale_for(pos.market)
        if not scale:
            continue
        cache = session.scalar(select(PriceCache).where(PriceCache.symbol == pos.symbol))
        last = float(cache.last) if cache is not None and cache.last else 0.0
        avg = float(pos.avg_price or 0.0)
        if not _looks_shifted(avg, last, scale):
            continue
        new_avg = avg * scale
        extra = (new_avg - avg) * pos.qty
        pos.avg_price = new_avg
        if pos.stop and pos.stop < new_avg * 0.5:
            pos.stop = pos.stop * scale
        if paper is not None:
            if pos.side == "buy":
                paper.cash -= extra
            else:
                paper.cash += extra
        fills = list(
            session.scalars(
                select(Fill).where(
                    Fill.symbol == pos.symbol,
                    Fill.venue == pos.venue,
                    Fill.side == pos.side,
                )
            )
        )
        for fill in fills:
            if fill.price and _looks_shifted(float(fill.price), last, scale):
                fill.price = float(fill.price) * scale
        fixed += 1
    return fixed
