from datetime import datetime
from zoneinfo import ZoneInfo

from meridian_v3.config import Settings
from meridian_v3.decision.engine import DecisionInput, decide
from meridian_v3.engine.confluence import FactorVote
from meridian_v3.engine.drawdown import assess_drawdown
from meridian_v3.engine.edge import CostEstimate
from meridian_v3.engine.meta_label import PrimarySignal


def _market_open_now():
    """A fixed Monday inside NSE hours (09:15-15:30 IST).

    These tests used `datetime.now()`, which made them depend on the real
    wall clock: once new paper entries were blocked in shut sessions
    (a shut venue has no executable price), the same test passed on a
    weekday afternoon and failed on a Sunday. Pin the clock so the test
    exercises the decision logic, not the calendar.
    """
    return datetime(2026, 8, 17, 11, 0, tzinfo=ZoneInfo("Asia/Kolkata"))


def _input(**kwargs):
    base = dict(
        symbol="INFY",
        price=1400,
        atr=20,
        created_at=_market_open_now(),
        primary=PrimarySignal(1, 1.4, "trend is up"),
        votes=[
            FactorVote("trend", 0.8, 1.0, "up"),
            FactorVote("breakout", 0.6, 0.8, "high"),
            FactorVote("score", 0.5, 0.7, "quality"),
        ],
        win_rupees=80,
        loss_rupees=30,
        costs=CostEstimate(1, 1, 1, 1),
        payoff=2.0,
        equity=50_000,
        cash=50_000,
        drawdown=assess_drawdown(50_000, 50_000),
        live_armed=False,
        live_today=0,
        open_count=0,
        now=_market_open_now(),
    )
    base.update(kwargs)
    return DecisionInput(**base)


def test_hold_when_no_primary():
    d = decide(_input(primary=PrimarySignal(0, 0.1, "flat")), Settings())
    assert d.action == "hold"
    assert d.paper is False
    assert d.live is False


def test_paper_not_live_when_disarmed():
    d = decide(_input(), Settings())
    assert d.action == "buy"
    assert d.paper is True
    assert d.live is False


def test_drawdown_blocks_live():
    d = decide(
        _input(drawdown=assess_drawdown(39_000, 50_000), live_armed=True),
        Settings(),
    )
    assert d.live is False
    assert any("paused" in r.lower() or "down" in r.lower() for r in d.safety.reasons)


def test_modelled_target_matches_the_target_the_exit_aims_at():
    """The pre-trade gate must price the trade the desk actually takes.

    `win` was a hardcoded 2.0 x ATR while the stop was 1.5 x ATR -- a 1.33:1
    model of what the exit treated as 2:1. Both sides now derive from the
    same two settings.
    """
    from meridian_v3.config import Settings

    s = Settings()
    atr = 100.0
    loss = atr * s.sizing.atr_stop_mult
    win = loss * s.sizing.target_r_multiple

    assert s.sizing.atr_stop_mult == 2.0
    assert s.sizing.target_r_multiple == 3.0
    assert loss == 200.0          # stop sits 2 x ATR away
    assert win == 600.0           # target sits 6 x ATR away
    assert win / loss == 3.0      # a 3:1 shape
    # Breakeven win rate at 3:1 is 25%, versus 33% at the old 2:1.
    assert abs(1 / (win / loss + 1) - 0.25) < 1e-9


def test_a_short_can_reach_its_target(session):
    """Both target branches used to test `pos.side == "buy"`, so a short
    could only ever exit on a stop or a tape flip -- never on reaching its
    target. A reward:risk shape that applies in one direction isn't one."""
    from datetime import datetime, timezone

    from meridian_v3.autopilot import _exit_reason
    from meridian_v3.config import Settings
    from meridian_v3.storage.schema import Position, PriceCache

    now = datetime.now(timezone.utc).replace(tzinfo=None)
    s = Settings()
    entry, stop_dist = 1000.0, 40.0          # stop 40 above -> target 120 below
    pos = Position(
        venue="paper", market="equity_cash", symbol="SHORTY", side="sell", qty=1,
        avg_price=entry, stop=stop_dist, status="open", horizon="positional",
        source="test", opened_at=now,
    )
    session.add(pos)
    session.add(PriceCache(symbol="SHORTY", last=entry, atr=20.0, as_of=now, quality="live"))
    session.flush()
    cache = session.query(PriceCache).filter_by(symbol="SHORTY").one()

    at_target = entry - stop_dist * s.sizing.target_r_multiple
    reason = _exit_reason(pos, at_target - 1, cache, flatten=False, session=session, settings=s)
    assert "Target hit" in reason, f"short never took its target: {reason!r}"
