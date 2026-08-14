from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Request
from sqlalchemy import select

from meridian_v3.config import get_settings
from meridian_v3.domain.templates import catalog_payload
from meridian_v3.risk.gamma_scalp import explain_scalp
from meridian_v3.risk.greeks_book import snapshot_from_legs
from meridian_v3.risk.vega_engine import OptionGreekLeg, VegaPolicy
from meridian_v3.risk.vega_strategies import HedgeUnit, plan_vega_actions
from meridian_v3.domain.reviews import compose_review
from meridian_v3.signals.chart_service import chart_for
from meridian_v3.storage.schema import EquityPoint, OptionLegRow, Position, SignalRow

router = APIRouter()


def _session(request: Request):
    return request.state.session


@router.get("/health")
def health() -> dict:
    return {"ok": True, "name": "MERIDIAN V3", "version": "3.0.0"}


@router.get("/catalog")
def catalog() -> dict:
    return catalog_payload()


@router.get("/chart/{symbol}")
def chart(symbol: str, request: Request) -> dict:
    return chart_for(_session(request), symbol)


@router.get("/review/{symbol}")
def review(symbol: str, request: Request) -> dict:
    session = _session(request)
    settings = get_settings()
    legs = [
        OptionGreekLeg(
            leg_id=row.leg_id,
            symbol=row.symbol,
            contract_label=row.contract_label,
            lots=row.lots,
            multiplier=row.multiplier,
            mark_inr=row.mark_inr,
            delta=row.delta,
            gamma=row.gamma,
            vega_per_lot=row.vega_per_lot,
            theta_per_lot=row.theta_per_lot,
            iv=row.iv,
            stale=bool(row.stale),
        )
        for row in session.scalars(select(OptionLegRow).where(OptionLegRow.symbol == symbol.upper()))
    ]
    snap = snapshot_from_legs(symbol.upper(), legs, date.today(), move_pct=settings.greeks.move_pct)
    policy = VegaPolicy(symbol=symbol.upper(), vega_limit=settings.greeks.vega_limit)
    unit = HedgeUnit(vega=55_000, delta=0.48, gamma=0.02, theta=-40)
    actions = plan_vega_actions(snap, policy, unit)
    review = compose_review(snap, vega_limit=settings.greeks.vega_limit)
    scalp = explain_scalp(snap, rehedge_band_lots=settings.greeks.rehedge_band_lots)
    return {
        "symbol": symbol.upper(),
        "as_of": snap.as_of.isoformat(),
        "stale": snap.stale,
        "greeks": {
            "delta_lots": snap.delta_lots,
            "gamma": snap.gamma,
            "gamma_sign": snap.gamma_sign,
            "vega": snap.vega,
            "theta": snap.theta,
            "daily_pnl": snap.daily_pnl,
            "gamma_scalp_pnl": snap.gamma_scalp_pnl,
            "move_pct": snap.move_pct,
            "scalp_helps": snap.scalp_helps,
        },
        "review": review.as_dict(),
        "gamma_scalp": scalp.as_dict(),
        "actions": [a.as_dict() for a in actions],
    }


@router.get("/equity")
def equity(request: Request) -> dict:
    session = _session(request)
    out = {"paper": [], "live": []}
    rows = session.scalars(select(EquityPoint).order_by(EquityPoint.as_of.asc()))
    for row in rows:
        out.setdefault(row.venue, []).append(
            {"time": row.as_of.isoformat(), "equity": row.equity, "cash": row.cash, "peak": row.peak}
        )
    return out


@router.get("/positions")
def positions(request: Request) -> dict:
    session = _session(request)
    rows = session.scalars(select(Position).where(Position.status == "open"))
    return {
        "open": [
            {
                "venue": r.venue,
                "market": r.market,
                "symbol": r.symbol,
                "side": r.side,
                "qty": r.qty,
                "avg_price": r.avg_price,
                "horizon": r.horizon,
            }
            for r in rows
        ]
    }


@router.get("/signals")
def signals(request: Request) -> dict:
    session = _session(request)
    rows = session.scalars(select(SignalRow).order_by(SignalRow.created_at.desc()).limit(40))
    return {
        "items": [
            {
                "symbol": r.symbol,
                "side": r.side,
                "confidence": r.confidence,
                "confluence": r.confluence,
                "paper": bool(r.paper),
                "live": bool(r.live),
                "reason": r.reason,
                "created_at": r.created_at.isoformat(),
            }
            for r in rows
        ]
    }
