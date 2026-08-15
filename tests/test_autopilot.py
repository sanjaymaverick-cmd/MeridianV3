from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from meridian_v3.autopilot import flatten_india_paper, manage_exits, set_paper_auto, tick
from meridian_v3.storage.schema import AccountState, BeliefRow, Position, PriceCache
from meridian_v3.storage.seed import seed_demo


def _now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _seed_one_open_india(session, *, last, avg=1400.0):
    now = _now()
    session.add(AccountState(venue="paper", cash=50_000, equity=50_000, peak=50_000, updated_at=now))
    # Part 3 item 4 — belief is keyed by market now; this position's market
    # is "equity_cash" (below), so the seeded prior must be too.
    session.add(BeliefRow(rule_name="equity_cash", alpha=4.0, beta=9.0, wins=0, losses=5))
    session.add(PriceCache(symbol="INFY", last=last, quality="live"))
    session.add(
        Position(
            venue="paper", market="equity_cash", symbol="INFY", side="buy",
            qty=2, avg_price=avg, stop=1000.0, horizon="intraday", status="open",
            source="test", opened_at=now,
        )
    )
    session.flush()


def test_eod_flatten_no_move_does_not_train_belief(session):
    """0.5 — a same-mark EOD flatten is a cost-only scratch, not a belief loss.

    Part 3 item 4 — belief is now keyed by market (``equity_cash`` here,
    matching the seeded position), not the old global ``"core"`` rule.
    """
    _seed_one_open_india(session, last=1400.0, avg=1400.0)
    ist_close = datetime(2026, 8, 17, 15, 20, tzinfo=ZoneInfo("Asia/Kolkata"))
    out = manage_exits(session, in_session=True, minutes_to_close=10, now=ist_close)
    assert out["closed"] == 1
    belief = session.query(BeliefRow).filter_by(rule_name="equity_cash").one()
    assert belief.losses == 5  # unchanged — the tape did not move
    assert belief.wins == 0


def test_eod_flatten_with_real_move_still_trains_belief(session):
    """0.5 — a flatten that actually moved is a genuine outcome and must train.

    Part 3 item 4 — belief is now keyed by market (``equity_cash`` here,
    matching the seeded position), not the old global ``"core"`` rule.
    """
    _seed_one_open_india(session, last=1460.0, avg=1400.0)
    ist_close = datetime(2026, 8, 17, 15, 20, tzinfo=ZoneInfo("Asia/Kolkata"))
    out = manage_exits(session, in_session=True, minutes_to_close=10, now=ist_close)
    assert out["closed"] == 1
    belief = session.query(BeliefRow).filter_by(rule_name="equity_cash").one()
    assert belief.wins == 1  # real +move booked a win


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
    monday = datetime(2026, 8, 17, 11, 0, tzinfo=ZoneInfo("Asia/Kolkata"))
    out = manage_exits(session, in_session=True, minutes_to_close=999, now=monday)
    assert out["closed"] == 1
    pos = session.query(Position).filter_by(symbol="INFY", venue="paper").one()
    assert pos.status == "closed"


def test_weekend_closes_india_and_returns_cash(session):
    seed_demo(session, reset=True)
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    paper = session.query(AccountState).filter_by(venue="paper").one()
    paper.cash = 1_000
    session.add(
        Position(
            venue="paper",
            market="equity_cash",
            symbol="INFY",
            side="buy",
            qty=8,
            avg_price=1169,
            stop=1000,
            horizon="intraday",
            status="open",
            source="test",
            opened_at=now,
        )
    )
    session.add(
        Position(
            venue="paper",
            market="crypto_spot",
            symbol="BTCUSDT",
            side="buy",
            qty=0.01,
            avg_price=80_000,
            stop=70_000,
            horizon="intraday",
            status="open",
            source="test",
            opened_at=now,
        )
    )
    cache = session.query(PriceCache).filter_by(symbol="INFY").one()
    cache.last = 1170
    session.flush()
    friday = datetime(2026, 8, 14, 16, 5, tzinfo=ZoneInfo("Asia/Kolkata"))
    out = flatten_india_paper(session, now=friday)
    assert out["india_closed"] == 1
    assert "INFY" in out["symbols"]
    infy = session.query(Position).filter_by(symbol="INFY", venue="paper").one()
    btc = session.query(Position).filter_by(symbol="BTCUSDT", venue="paper").one()
    assert infy.status == "closed"
    assert btc.status == "open"
    paper = session.query(AccountState).filter_by(venue="paper").one()
    assert paper.cash > 9_000


def test_india_stays_open_during_cash_session(session):
    seed_demo(session, reset=True)
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    session.add(
        Position(
            venue="paper",
            market="equity_cash",
            symbol="INFY",
            side="buy",
            qty=2,
            avg_price=1400,
            stop=1000,
            horizon="intraday",
            status="open",
            source="test",
            opened_at=now,
        )
    )
    session.flush()
    monday = datetime(2026, 8, 17, 11, 30, tzinfo=ZoneInfo("Asia/Kolkata"))
    out = flatten_india_paper(session, now=monday)
    assert out["india_closed"] == 0
    pos = session.query(Position).filter_by(symbol="INFY", venue="paper").one()
    assert pos.status == "open"
