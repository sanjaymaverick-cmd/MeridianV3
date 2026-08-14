from datetime import datetime, timezone

from meridian_v3.config import Settings
from meridian_v3.decision.engine import DecisionInput, decide
from meridian_v3.engine.confluence import FactorVote
from meridian_v3.engine.drawdown import assess_drawdown
from meridian_v3.engine.edge import CostEstimate
from meridian_v3.engine.meta_label import PrimarySignal


def _input(**kwargs):
    base = dict(
        symbol="INFY",
        price=1400,
        atr=20,
        created_at=datetime.now(timezone.utc),
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
        equity=5000,
        cash=5000,
        drawdown=assess_drawdown(5000, 5000),
        live_armed=False,
        live_today=0,
        open_count=0,
        equity_score=80,
        options_score=20,
        forex_score=10,
        now=datetime.now(timezone.utc),
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
        _input(drawdown=assess_drawdown(3900, 5000), live_armed=True),
        Settings(),
    )
    assert d.live is False
    assert any("paused" in r.lower() or "down" in r.lower() for r in d.safety.reasons)
