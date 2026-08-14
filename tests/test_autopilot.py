from datetime import datetime, timezone

from meridian_v3.autopilot import manage_exits, set_paper_auto, tick
from meridian_v3.storage.schema import Position
from meridian_v3.storage.seed import seed_demo


def test_tick_respects_auto_off(session):
    seed_demo(session, reset=True)
    set_paper_auto(session, False)
    out = tick(session, refresh_prices=False)
    assert out["ok"] is False
    assert "off" in out["note"].lower()


def test_tick_runs_when_auto_on(session):
    seed_demo(session, reset=True)
    set_paper_auto(session, True)
    out = tick(session, refresh_prices=False)
    assert out["ok"] is True
    assert "paper fills" in out["note"].lower() or "new paper" in out["note"].lower()


def test_stop_closes_and_trains(session):
    seed_demo(session, reset=True)
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    session.add(
        Position(
            venue="paper",
            market="equity_cash",
            symbol="INFY",
            side="buy",
            qty=1,
            avg_price=2000,
            stop=1500,
            horizon="intraday",
            status="open",
            source="test",
            opened_at=now,
        )
    )
    session.flush()
    from meridian_v3.storage.schema import PriceCache

    cache = session.query(PriceCache).filter_by(symbol="INFY").one()
    cache.last = 1400
    out = manage_exits(session, in_session=False, minutes_to_close=999)
    assert out["closed"] == 1
    pos = session.query(Position).filter_by(symbol="INFY", venue="paper").one()
    assert pos.status == "closed"
