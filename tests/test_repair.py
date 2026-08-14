from datetime import datetime, timezone

import pytest

from meridian_v3.autopilot import manage_exits
from meridian_v3.storage.repair import align_prices, repair_shifted_clips
from meridian_v3.storage.schema import AccountState, Fill, Position, PriceCache


def _now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def test_align_prices_moves_decimal_on_silver():
    entry, exit_px, scale = align_prices(621.82, 6218.20)
    assert scale == 10.0
    assert entry == pytest.approx(6218.2)
    assert exit_px == pytest.approx(6218.20)
    # Sold 6,228.70, covered 6,216.29 — short is a small win, not −₹224.
    sold, cover, scale = align_prices(622.87, 6216.29)
    assert scale == 10.0
    pnl = -((cover - sold) * 0.04)
    assert pnl == pytest.approx(0.496, abs=0.01)


def _paper(session, cash=50_000.0):
    session.add(
        AccountState(
            venue="paper",
            cash=cash,
            equity=cash,
            peak=cash,
            updated_at=_now(),
        )
    )


def test_repairs_silver_short_booked_at_one_tenth(session):
    _paper(session, 30_000)
    session.add(PriceCache(symbol="SILVER.X", last=6228.69, quality="live"))
    opened = _now()
    session.add(
        Position(
            venue="paper",
            market="global_commodities",
            symbol="SILVER.X",
            side="sell",
            qty=0,
            avg_price=622.87,
            close_qty=0.04,
            exit_price=6228.69,
            realized_pnl=-224.32,
            status="closed",
            source="test",
            opened_at=opened,
            closed_at=opened,
        )
    )
    session.add(
        Fill(
            venue="paper",
            symbol="SILVER.X",
            side="sell",
            qty=0.04,
            price=622.87,
            fees=0.01,
            filled_at=opened,
            note="PAPER FILL: SELL 0.04 SILVER.X near ₹622.87.",
        )
    )
    session.add(
        Fill(
            venue="paper",
            symbol="SILVER.X",
            side="buy",
            qty=0.04,
            price=6228.69,
            fees=0.09,
            filled_at=opened,
            note="PAPER EXIT: Stop hit.  P&L ₹-224 after fees ₹0.09.",
        )
    )
    session.flush()

    assert repair_shifted_clips(session) == 1
    pos = session.query(Position).one()
    assert pos.avg_price == pytest.approx(6228.7, abs=1.0)
    assert abs(pos.realized_pnl) < 1.0
    entry = session.query(Fill).filter_by(side="sell").one()
    assert entry.price == pytest.approx(6228.7, abs=1.0)
    assert repair_shifted_clips(session) == 0


def test_repairs_gold_long_and_leaves_honest_silver_alone(session):
    _paper(session)
    session.add(
        Position(
            venue="paper",
            market="global_commodities",
            symbol="GOLD.X",
            side="buy",
            qty=0,
            avg_price=42_313.69,
            close_qty=0.02,
            exit_price=423_642.60,
            realized_pnl=7_623.58,
            status="closed",
            source="test",
            opened_at=_now(),
            closed_at=_now(),
        )
    )
    session.add(
        Position(
            venue="paper",
            market="global_commodities",
            symbol="SILVER.X",
            side="sell",
            qty=0,
            avg_price=6_221.54,
            close_qty=1.28,
            exit_price=6_221.54,
            realized_pnl=-2.82,
            status="closed",
            source="test",
            opened_at=_now(),
            closed_at=_now(),
        )
    )
    session.flush()
    n = repair_shifted_clips(session)
    gold = session.query(Position).filter_by(symbol="GOLD.X").one()
    silver = session.query(Position).filter_by(symbol="SILVER.X").one()
    assert n == 1
    assert gold.avg_price == pytest.approx(423_136.9, abs=5)
    assert abs(gold.realized_pnl) < 20
    assert silver.avg_price == 6_221.54
    assert silver.realized_pnl == -2.82


def test_repairs_india_future_margin_over_qty(session):
    _paper(session)
    session.add(
        Position(
            venue="paper",
            market="india_futures",
            symbol="NIFTY.F",
            side="buy",
            qty=0,
            avg_price=7936.8,
            close_qty=0.05,
            exit_price=24407.19,
            realized_pnl=800,
            status="closed",
            source="test",
            opened_at=_now(),
            closed_at=_now(),
        )
    )
    session.flush()
    assert repair_shifted_clips(session) == 1
    pos = session.query(Position).one()
    assert pos.avg_price == pytest.approx(24421, abs=30)
    assert abs(pos.realized_pnl) < 5


def test_commodity_short_does_not_stop_on_atr_distance(session):
    from zoneinfo import ZoneInfo

    _paper(session)
    session.add(PriceCache(symbol="SILVER.X", last=6216.29, quality="live"))
    session.add(
        Position(
            venue="paper",
            market="global_commodities",
            symbol="SILVER.X",
            side="sell",
            qty=0.04,
            avg_price=6216.29,
            stop=219.16,
            horizon="intraday",
            status="open",
            source="test",
            opened_at=_now(),
        )
    )
    session.flush()
    monday = datetime(2026, 8, 17, 11, 0, tzinfo=ZoneInfo("Asia/Kolkata"))
    out = manage_exits(session, in_session=True, minutes_to_close=999, now=monday)
    assert out["closed"] == 0
    pos = session.query(Position).one()
    assert pos.status == "open"
