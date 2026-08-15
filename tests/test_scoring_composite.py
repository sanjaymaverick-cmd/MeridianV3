"""1.2 — the V1 five-factor score must be a live input, not a dead constant.

Before this fix, `factor_scores` had zero rows ever written to it (nothing
under `meridian_v3/` imported `scoring/composite.py`), so `pipeline.py`
always fell back to the literal constant `6.5` for every symbol on every
cycle (F5). These tests exercise the real wiring: `scoring.composite` is
called from the cycle path and writes real `FactorScore` rows that vary by
symbol.
"""

from __future__ import annotations

from datetime import datetime, timezone

from meridian_v3.scoring.composite import factor_parts_from_tape
from meridian_v3.storage.schema import FactorScore
from meridian_v3.storage.seed import seed_demo


def test_factor_parts_vary_with_the_tape():
    strong = factor_parts_from_tape(
        last=120.0, sma20=118.0, sma50=110.0, high20=125.0, low20=100.0, rsi_value=40.0
    )
    weak = factor_parts_from_tape(
        last=95.0, sma20=100.0, sma50=110.0, high20=125.0, low20=100.0, rsi_value=75.0
    )
    assert strong["technical"] is not None and weak["technical"] is not None
    assert strong["technical"] > weak["technical"]
    # No fundamentals feed exists in this desk — those factors stay honestly None.
    assert strong["quality"] is None
    assert strong["ownership"] is None
    assert strong["sentiment"] is None


def test_run_cycle_writes_factor_scores_that_vary_by_symbol(session):
    from meridian_v3.pipeline import run_cycle

    seed_demo(session, reset=True)
    monday = datetime(2026, 8, 17, 5, 45, tzinfo=timezone.utc)
    run_cycle(session, now=monday)

    rows = session.query(FactorScore).all()
    assert len(rows) > 0
    by_symbol = {row.symbol: row.composite for row in rows}
    assert len(by_symbol) > 1, "expected more than one symbol scored"
    assert len(set(by_symbol.values())) > 1, "composite scores must vary by symbol, not one fixed constant"
