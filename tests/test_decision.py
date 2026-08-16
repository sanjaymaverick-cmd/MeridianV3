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
