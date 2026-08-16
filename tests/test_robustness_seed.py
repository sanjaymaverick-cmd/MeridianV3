"""Seeding the walk-forward gate from a backtest.

`_market_robustness` scores a market by walking its closed *paper* positions
forward. A fresh book has almost none, so every market returned "Not enough
walk-forward folds" -> robust=False -> a 0.7x confidence penalty on every
decision, indefinitely. That is a deadlock: the check needs closed trades,
and having none suppresses the confidence required to open any.

A backtest replaying the real pipeline over years of HistoricalBar produces
exactly the P&L series the check wants, so it can seed the cold start. The
rules this locks in: live wins whenever it has real folds, the seed is only
a fallback, and a seeded verdict says in words that it came from a backtest.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from meridian_v3.config import get_settings
from meridian_v3.pipeline import _market_robustness
from meridian_v3.storage.schema import Position, RobustnessSnapshot


def _snapshot(session, market: str, *, robust: bool, folds: int = 4):
    session.add(
        RobustnessSnapshot(
            market=market, is_score=0.61, oos_score=0.58, gap=0.05,
            robust=1 if robust else 0, folds=folds, trades=300,
            reason="Out-of-sample still makes money. (Seeded from a backtest — not live evidence.)",
            source="backtest", computed_at=datetime.now(timezone.utc).replace(tzinfo=None),
        )
    )
    session.flush()


def _closed(session, market: str, n: int, *, pnl: float = 10.0):
    base = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=n + 1)
    for i in range(n):
        session.add(
            Position(
                venue="paper", market=market, symbol=f"S{i}", side="buy", qty=0,
                avg_price=100.0, close_qty=1, exit_price=100.0 + pnl, realized_pnl=pnl,
                status="closed", source="test",
                opened_at=base + timedelta(minutes=i), closed_at=base + timedelta(minutes=i),
            )
        )
    session.flush()


def test_no_live_history_and_no_seed_stays_conservative(session):
    """Unchanged behaviour where nothing is known: refuse to claim robustness."""
    verdict = _market_robustness(session, "equity_cash", get_settings())
    assert verdict.robust is False
    assert "not enough" in verdict.reason.lower()


def test_a_seeded_verdict_is_used_when_there_is_no_live_history(session):
    _snapshot(session, "equity_cash", robust=True)
    verdict = _market_robustness(session, "equity_cash", get_settings())
    assert verdict.robust is True
    assert verdict.folds == 4
    # It must say plainly that this is not live evidence.
    assert "backtest" in verdict.reason.lower()


def test_a_seed_does_not_leak_across_markets(session):
    _snapshot(session, "equity_cash", robust=True)
    other = _market_robustness(session, "crypto_spot", get_settings())
    assert other.robust is False
    assert "not enough" in other.reason.lower()


def test_live_history_overrides_the_seed_once_there_are_real_folds(session):
    """The seed is a cold start, not a permanent override. 82 closed trades
    is one fold with the default train=60/embargo=2/test=20."""
    _snapshot(session, "equity_cash", robust=True, folds=9)
    _closed(session, "equity_cash", 82, pnl=-5.0)  # a genuinely losing live record

    verdict = _market_robustness(session, "equity_cash", get_settings())
    # Live data produced the verdict, so the backtest wording is gone ...
    assert "backtest" not in verdict.reason.lower()
    # ... and a market that loses every live trade is not robust, whatever
    # the seed claimed.
    assert verdict.robust is False


def test_seeded_verdict_reaches_decide_and_lifts_the_confidence_penalty(session):
    """End-to-end: `decide()` applies a 0.7x haircut when robustness is not
    robust. A seed that says otherwise must remove that haircut — this is
    the whole point of seeding."""
    from datetime import datetime as _dt
    from zoneinfo import ZoneInfo

    from meridian_v3.config import Settings
    from meridian_v3.decision.engine import DecisionInput, decide
    from meridian_v3.engine.confluence import FactorVote
    from meridian_v3.engine.drawdown import assess_drawdown
    from meridian_v3.engine.edge import estimate_equity_costs
    from meridian_v3.engine.meta_label import PrimarySignal

    now = _dt(2026, 8, 17, 11, 0, tzinfo=ZoneInfo("Asia/Kolkata"))

    def _confidence(robustness_verdict):
        return decide(
            DecisionInput(
                symbol="INFY", price=1400, atr=28, created_at=now,
                primary=PrimarySignal(1, 1.4, "trend is up"),
                votes=[
                    FactorVote("trend", 0.8, 1.0, "up"),
                    FactorVote("breakout", 0.6, 0.8, "high"),
                    FactorVote("score", 0.5, 0.7, "quality"),
                ],
                win_rupees=56, loss_rupees=42,
                costs=estimate_equity_costs(notional=1400), payoff=1.33,
                equity=100_000, cash=100_000,
                drawdown=assess_drawdown(100_000, 100_000),
                live_armed=False, live_today=0, open_count=0,
                preferred_market="equity_cash", now=now,
                robustness=robustness_verdict,
            ),
            Settings(),
        ).confidence

    _snapshot(session, "equity_cash", robust=True)
    seeded = _market_robustness(session, "equity_cash", get_settings())
    unseeded = _market_robustness(session, "crypto_spot", get_settings())

    assert _confidence(seeded) > _confidence(unseeded)
    # The haircut is exactly the 0.7x decide() applies.
    assert abs(_confidence(unseeded) / _confidence(seeded) - 0.7) < 1e-6
