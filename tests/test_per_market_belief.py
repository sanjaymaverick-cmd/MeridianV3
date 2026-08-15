"""Part 3 item 4 — belief tracking must be per market, not one shared "core" row.

Before this fix, `pipeline._belief` and `pipeline.persist_belief` always keyed
off the single global rule name `"core"`, so an equity win could move the
same Beta(alpha, beta) prior a crypto or futures decision was scored
against. These tests exercise the real wiring: `_belief`/`persist_belief`
keyed by market, `autopilot._train_from_close` reading `Position.market`
instead of a hardcoded `"core"`, and `run_cycle` actually handing each
symbol the belief for *its own* routed market.
"""

from __future__ import annotations

from datetime import datetime, timezone

from meridian_v3.autopilot import _train_from_close
from meridian_v3.pipeline import _belief, persist_belief
from meridian_v3.storage.schema import BeliefRow, Position
from meridian_v3.storage.seed import seed_demo


def _now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def test_belief_cold_start_is_independent_per_market(session):
    """Two markets with no row yet both get the same cold-start prior, but
    as independent objects — moving one must not be able to move the other
    (proven further in the next test)."""
    equity = _belief(session, "equity_cash")
    crypto = _belief(session, "crypto_spot")
    assert (equity.alpha, equity.beta, equity.wins, equity.losses) == (4.0, 4.0, 0, 0)
    assert (crypto.alpha, crypto.beta, crypto.wins, crypto.losses) == (4.0, 4.0, 0, 0)
    assert equity is not crypto


def test_persist_belief_only_moves_its_own_market_row(session):
    persist_belief(session, won=True, rule="equity_cash")
    persist_belief(session, won=True, rule="equity_cash")
    persist_belief(session, won=False, rule="crypto_spot")
    session.flush()

    equity_row = session.query(BeliefRow).filter_by(rule_name="equity_cash").one()
    crypto_row = session.query(BeliefRow).filter_by(rule_name="crypto_spot").one()
    assert equity_row.wins == 2
    assert equity_row.losses == 0
    assert crypto_row.wins == 0
    assert crypto_row.losses == 1

    # A further equity_cash update must leave the separately-tracked
    # crypto_spot row completely unchanged.
    persist_belief(session, won=True, rule="equity_cash")
    session.flush()
    crypto_row_after = session.query(BeliefRow).filter_by(rule_name="crypto_spot").one()
    assert crypto_row_after.wins == 0
    assert crypto_row_after.losses == 1
    equity_row_after = session.query(BeliefRow).filter_by(rule_name="equity_cash").one()
    assert equity_row_after.wins == 3


def test_train_from_close_keys_off_position_market_not_hardcoded_core(session):
    """`_train_from_close` (autopilot.py) must read `pos.market`, not a
    hardcoded `"core"` rule — this is the actual write path every real
    paper-clip close goes through."""
    now = _now()
    pos = Position(
        venue="paper",
        market="crypto_futures",
        symbol="BTCUSDT.F",
        side="buy",
        qty=0,
        avg_price=100.0,
        close_qty=1,
        exit_price=110.0,
        realized_pnl=10.0,
        status="closed",
        source="test",
        opened_at=now,
        closed_at=now,
        feature_json="{}",
    )
    session.add(pos)
    session.flush()

    _train_from_close(session, pos, won=True)
    session.flush()

    crypto_futures_row = session.query(BeliefRow).filter_by(rule_name="crypto_futures").one()
    assert crypto_futures_row.wins == 1
    assert crypto_futures_row.losses == 0
    # The old global rule must never get written by this path anymore.
    assert session.query(BeliefRow).filter_by(rule_name="core").first() is None


def test_train_from_close_does_not_cross_contaminate_a_second_market(session):
    now = _now()
    equity_pos = Position(
        venue="paper", market="equity_cash", symbol="RELIANCE", side="buy", qty=0,
        avg_price=100.0, close_qty=1, exit_price=95.0, realized_pnl=-5.0,
        status="closed", source="test", opened_at=now, closed_at=now, feature_json="{}",
    )
    crypto_pos = Position(
        venue="paper", market="crypto_spot", symbol="BTCUSDT", side="buy", qty=0,
        avg_price=100.0, close_qty=1, exit_price=120.0, realized_pnl=20.0,
        status="closed", source="test", opened_at=now, closed_at=now, feature_json="{}",
    )
    session.add_all([equity_pos, crypto_pos])
    session.flush()

    _train_from_close(session, equity_pos, won=False)
    _train_from_close(session, crypto_pos, won=True)
    session.flush()

    equity_row = session.query(BeliefRow).filter_by(rule_name="equity_cash").one()
    crypto_row = session.query(BeliefRow).filter_by(rule_name="crypto_spot").one()
    assert equity_row.losses == 1 and equity_row.wins == 0
    assert crypto_row.wins == 1 and crypto_row.losses == 0


def test_run_cycle_belief_differs_between_symbols_in_different_markets(session, monkeypatch):
    """End-to-end: seed one market's belief pessimistic and another's
    optimistic, run a real cycle, and confirm `DecisionInput.belief`
    (captured via a spy on `pipeline.decide`) actually differs between a
    symbol routed to each market — proving `run_cycle` hands each symbol
    the belief for *its own* routed market, not one shared row.
    """
    seed_demo(session, reset=True)
    # RELIANCE (equity, DEMO_WATCH) -> equity_cash. USDINR (fx) -> forex_micro.
    # Beta(alpha, beta) mean is alpha / (alpha + beta) — 40 straight losses
    # from the Beta(4, 4) cold start pushes beta to 44 (mean ~0.08); 40
    # straight wins pushes alpha to 44 (mean ~0.92).
    session.add(BeliefRow(rule_name="equity_cash", alpha=4.0, beta=44.0, wins=0, losses=40))
    session.add(BeliefRow(rule_name="forex_micro", alpha=44.0, beta=4.0, wins=40, losses=0))
    session.flush()

    from meridian_v3 import pipeline

    real_decide = pipeline.decide
    captured: dict[str, object] = {}

    def spy(inp, settings):
        captured[inp.symbol] = inp.belief
        return real_decide(inp, settings)

    monkeypatch.setattr(pipeline, "decide", spy)
    monday = datetime(2026, 8, 17, 5, 45, tzinfo=timezone.utc)
    pipeline.run_cycle(session, now=monday)

    assert "RELIANCE" in captured and "USDINR" in captured
    reliance_belief = captured["RELIANCE"]
    usdinr_belief = captured["USDINR"]
    assert reliance_belief.mean < 0.5  # pessimistic equity_cash prior
    assert usdinr_belief.mean > 0.5  # optimistic forex_micro prior
    assert reliance_belief.mean != usdinr_belief.mean


def test_decide_confidence_moves_with_belief_for_otherwise_identical_signals():
    """Direct `decide()` check (not run_cycle) that a more optimistic belief
    produces measurably higher confidence than a pessimistic one, holding
    every other input fixed — this is the mechanism the run_cycle-level
    test above relies on to prove the per-market split matters."""
    from meridian_v3.config import Settings
    from meridian_v3.decision.engine import DecisionInput, decide
    from meridian_v3.engine.bayesian import BetaBelief
    from meridian_v3.engine.confluence import FactorVote
    from meridian_v3.engine.drawdown import assess_drawdown
    from meridian_v3.engine.edge import CostEstimate
    from meridian_v3.engine.meta_label import PrimarySignal

    def _input(belief):
        return DecisionInput(
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
            equity=50_000,
            cash=50_000,
            drawdown=assess_drawdown(50_000, 50_000),
            live_armed=False,
            live_today=0,
            open_count=0,
            now=datetime.now(timezone.utc),
            belief=belief,
        )

    pessimistic = BetaBelief(alpha=4.0, beta=44.0, wins=0, losses=40)
    optimistic = BetaBelief(alpha=44.0, beta=4.0, wins=40, losses=0)
    assert pessimistic.mean < optimistic.mean

    d_pessimistic = decide(_input(pessimistic), Settings())
    d_optimistic = decide(_input(optimistic), Settings())
    assert d_optimistic.confidence > d_pessimistic.confidence
