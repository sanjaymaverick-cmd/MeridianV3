"""Part 3 item 5 — wire the walk-forward robustness harness into the live cycle.

Before this fix, `engine/walkforward.py` was fully written and unit-tested in
isolation, but nothing in `pipeline.run_cycle` ever called it — every
`DecisionInput.robustness` was `None` forever, even though `decide()`
already consumed it (`confidence *= 0.7` when `not robustness.robust`).
These tests exercise `pipeline._market_robustness` (the new per-market
walk-forward helper), its caching inside `run_cycle` (at most one DB query +
computation per distinct market per cycle, not one per symbol), and the
real end-to-end effect on `confidence` once it reaches `decide()`.
"""

from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone

import pytest

from meridian_v3.config import get_settings
from meridian_v3.pipeline import _market_robustness
from meridian_v3.storage.schema import Position
from meridian_v3.storage.seed import seed_demo


def _now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _closed_position(session, *, market, pnl, closed_at, symbol="TEST"):
    session.add(
        Position(
            venue="paper",
            market=market,
            symbol=symbol,
            side="buy",
            qty=0,
            avg_price=100.0,
            close_qty=1,
            exit_price=100.0 + pnl,
            realized_pnl=pnl,
            status="closed",
            source="test",
            opened_at=closed_at,
            closed_at=closed_at,
        )
    )


# ---------------------------------------------------------------------------
# `_market_robustness` — not enough history
# ---------------------------------------------------------------------------


def test_too_few_closed_positions_falls_back_to_not_enough_folds(session):
    """Well under the 82-minimum (train=60, embargo=2, test=20) — this must
    hit `robustness([], [], ...)`'s own conservative fallback, not some
    invented lower-data path."""
    from datetime import timedelta

    settings = get_settings()
    base = _now()
    for i in range(10):
        _closed_position(
            session, market="crypto_spot", pnl=10.0 if i % 2 == 0 else -10.0,
            closed_at=base + timedelta(minutes=i), symbol=f"C{i}",
        )
    session.flush()

    result = _market_robustness(session, "crypto_spot", settings)
    assert result.folds == 0
    assert result.robust is False
    assert "not enough" in result.reason.lower()


def test_unreconciled_positions_without_realized_pnl_are_excluded(session):
    """`status="closed"` rows with no honest `realized_pnl` (Phase 0's
    unreconciled repairs use `status="unreconciled"`, a different status
    entirely, but a belt-and-braces None-guard is still worth proving)."""
    from datetime import timedelta

    settings = get_settings()
    base = _now()
    for i in range(90):
        session.add(
            Position(
                venue="paper", market="india_futures", symbol=f"F{i}", side="buy", qty=0,
                avg_price=100.0, close_qty=1, exit_price=None, realized_pnl=None,
                status="closed", source="test", opened_at=base + timedelta(minutes=i),
                closed_at=base + timedelta(minutes=i),
            )
        )
    session.flush()

    result = _market_robustness(session, "india_futures", settings)
    assert result.folds == 0
    assert result.robust is False


# ---------------------------------------------------------------------------
# `_market_robustness` — real math once there is enough history (>= 82)
# ---------------------------------------------------------------------------


def _build_82(oos_similar: bool):
    """60 train + 2 embargo + 20 test = 82, the minimum for exactly one fold
    with `walk_forward_folds`'s defaults. Train hit rate is a fixed 40/60
    (~0.667). `oos_similar=True` keeps the test window close to that
    (12/20 = 0.6, a 10% gap — robust). `oos_similar=False` makes the test
    window a total washout (0/20 — a 100% gap and oos_mean == 0 — the
    textbook curve-fit case)."""
    train = [10.0] * 40 + [-10.0] * 20
    embargo = [5.0, 5.0]
    test = ([10.0] * 12 + [-10.0] * 8) if oos_similar else [-10.0] * 20
    return train + embargo + test


def test_similar_oos_performance_is_robust(session):
    from datetime import timedelta

    settings = get_settings()
    base = _now()
    pnls = _build_82(oos_similar=True)
    for i, pnl in enumerate(pnls):
        _closed_position(session, market="equity_cash", pnl=pnl, closed_at=base + timedelta(minutes=i), symbol=f"E{i}")
    session.flush()

    result = _market_robustness(session, "equity_cash", settings)
    assert result.folds == 1
    assert result.oos_score == pytest.approx(0.6)
    assert result.is_score == pytest.approx(40 / 60)
    assert result.robust is True


def test_much_worse_oos_performance_looks_curve_fit(session):
    from datetime import timedelta

    settings = get_settings()
    base = _now()
    pnls = _build_82(oos_similar=False)
    for i, pnl in enumerate(pnls):
        _closed_position(session, market="equity_cash", pnl=pnl, closed_at=base + timedelta(minutes=i), symbol=f"E{i}")
    session.flush()

    result = _market_robustness(session, "equity_cash", settings)
    assert result.folds == 1
    assert result.oos_score == pytest.approx(0.0)
    assert result.is_score == pytest.approx(40 / 60)
    assert result.gap == pytest.approx(1.0)
    assert result.robust is False
    assert "curve-fit" in result.reason.lower()


# ---------------------------------------------------------------------------
# Wiring: `DecisionInput.robustness` actually reaches `decide()` via run_cycle
# ---------------------------------------------------------------------------


def test_run_cycle_caches_robustness_at_most_once_per_market(session, monkeypatch):
    """DEMO_WATCH has six equity symbols (RELIANCE, HDFCBANK, INFY, TCS,
    TMPV, GOLD) that all route to `equity_cash`. `_market_robustness`
    must be called at most once for that market across the whole cycle, not
    once per symbol."""
    seed_demo(session, reset=True)

    from meridian_v3 import pipeline

    calls: list[str] = []
    real = pipeline._market_robustness

    def spy(session_, market, settings):
        calls.append(market)
        return real(session_, market, settings)

    monkeypatch.setattr(pipeline, "_market_robustness", spy)
    monday = datetime(2026, 8, 17, 5, 45, tzinfo=timezone.utc)
    pipeline.run_cycle(session, now=monday)

    counts = Counter(calls)
    assert counts["equity_cash"] == 1, f"expected exactly one equity_cash computation, got {counts}"
    assert all(n == 1 for n in counts.values())
    assert len(counts) <= 8


def test_robust_false_lowers_confidence_end_to_end(tmp_path, monkeypatch):
    """Prove the field is actually wired, not just consumed in isolation:
    two otherwise-identical `run_cycle` calls against fresh, identically
    seeded books, differing only in what `_market_robustness` is
    monkeypatched to return, must produce a measurably different
    `SignalRow.confidence` for the same symbol — the `* 0.7` `decide()`
    already applies when `robustness.robust` is False.
    """
    from meridian_v3.config import get_settings, reset_settings_cache
    from meridian_v3.engine.walkforward import Robustness
    from meridian_v3.storage.db import get_session, init_db, reset_engine
    from meridian_v3.storage.schema import SignalRow

    def _fresh_session(name):
        db = tmp_path / f"{name}.sqlite"
        monkeypatch.setenv("MERIDIAN_V3_TEST_DB", str(db))
        reset_settings_cache()
        reset_engine()
        s = get_settings()
        s.test_db = str(db)
        init_db()
        return get_session()

    robust_true = Robustness(is_score=0.62, oos_score=0.58, gap=0.06, robust=True, folds=1, reason="ok")
    robust_false = Robustness(is_score=0.62, oos_score=0.0, gap=1.0, robust=False, folds=1, reason="curve-fit")
    monday = datetime(2026, 8, 17, 5, 45, tzinfo=timezone.utc)

    from meridian_v3 import pipeline

    session_a = _fresh_session("robust_true")
    seed_demo(session_a, reset=True)
    monkeypatch.setattr(pipeline, "_market_robustness", lambda s, m, st: robust_true)
    pipeline.run_cycle(session_a, now=monday)
    row_a = (
        session_a.query(SignalRow).filter_by(symbol="RELIANCE").order_by(SignalRow.id.desc()).first()
    )
    session_a.commit()
    session_a.close()
    reset_engine()
    reset_settings_cache()

    session_b = _fresh_session("robust_false")
    seed_demo(session_b, reset=True)
    monkeypatch.setattr(pipeline, "_market_robustness", lambda s, m, st: robust_false)
    pipeline.run_cycle(session_b, now=monday)
    row_b = (
        session_b.query(SignalRow).filter_by(symbol="RELIANCE").order_by(SignalRow.id.desc()).first()
    )
    session_b.commit()
    session_b.close()
    reset_engine()
    reset_settings_cache()

    assert row_a is not None and row_b is not None
    assert row_b.confidence < row_a.confidence
    assert row_b.confidence == pytest.approx(row_a.confidence * 0.7, rel=1e-6)


def test_a_consistently_unprofitable_rule_is_not_robust():
    """`oos_mean > 0` only asked whether the rule ever won.

    A rule hitting 10% with a 3:1 payoff bleeds money steadily, but it used
    to pass the robustness gate so long as it didn't degrade from in-sample
    — the check tested consistency and called it profitability. It must now
    clear the breakeven hit rate for the payoff actually traded.
    """
    from meridian_v3.engine.walkforward import robustness

    # Consistent (no gap) but far below the 25% breakeven for 3:1.
    verdict = robustness([0.11], [0.10], max_gap=0.35, min_oos=0.25)
    assert verdict.robust is False
    assert "does not pay for itself" in verdict.reason
    # Under the old rule this exact case passed.
    assert robustness([0.11], [0.10], max_gap=0.35, min_oos=0.0).robust is True


def test_breakeven_tracks_the_payoff_being_traded():
    """A wider payoff tolerates a lower hit rate, and the floor must move
    with it rather than being a fixed number."""
    from meridian_v3.config import Settings
    from meridian_v3.pipeline import _breakeven_hit_rate

    s = Settings()
    assert _breakeven_hit_rate(s) == pytest.approx(0.25)  # 3:1

    s.sizing.target_r_multiple = 1.0
    assert _breakeven_hit_rate(s) == pytest.approx(0.50)  # 1:1 needs half
    s.sizing.target_r_multiple = 9.0
    assert _breakeven_hit_rate(s) == pytest.approx(0.10)  # 9:1 tolerates 10%


def test_a_curve_fit_is_still_named_as_one():
    """When a rule both collapses out-of-sample AND lands under breakeven,
    the collapse is the more useful diagnosis — it says why."""
    from meridian_v3.engine.walkforward import robustness

    verdict = robustness([0.67], [0.0], max_gap=0.35, min_oos=0.25)
    assert verdict.robust is False
    assert "curve-fit" in verdict.reason.lower()
