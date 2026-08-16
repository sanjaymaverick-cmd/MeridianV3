"""backtest/engine.py — replays the real pipeline (decide/OMS/fees/belief
training) against HistoricalBar data on an isolated DB. These tests seed a
small synthetic price series rather than depending on the real backfilled
data (CI never runs backfill-history), and verify the engine doesn't leave
the process's DB context broken after it switches to (and back from) its
own isolated simulation database."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

from meridian_v3.backtest.engine import run_backtest
from meridian_v3.storage.schema import HistoricalBar


def _seed_trend(session, symbol: str, days: int, start_price: float = 100.0, step: float = 0.3):
    base = date.today() - timedelta(days=days + 5)
    d = base
    price = start_price
    written = 0
    while written < days:
        if d.weekday() < 5:  # weekdays only, matching real trading calendars
            session.add(
                HistoricalBar(
                    symbol=symbol, bar_date=d, open=price, high=price + 1, low=price - 1,
                    close=price, volume=10_000.0, source="test",
                )
            )
            price += step
            written += 1
        d += timedelta(days=1)
    session.commit()  # run_backtest() reads via a *separate* session object
    # (its own get_session() call) -- a flush() alone is only visible within
    # this same session/transaction, not across connections.
    return base, d


def test_run_backtest_produces_a_real_equity_curve(session):
    start, end = _seed_trend(session, "TESTSYM", days=90)
    result = run_backtest(["TESTSYM"], start=start, end=end, starting_capital=50_000.0)

    assert result.symbols == ["TESTSYM"]
    assert result.trading_days_simulated > 0
    assert len(result.equity_curve) == result.trading_days_simulated
    assert result.starting_capital == 50_000.0
    # equity_curve is chronological
    dates = [d for d, _ in result.equity_curve]
    assert dates == sorted(dates)


def test_run_backtest_leaves_the_original_db_context_usable_after(session):
    """The engine switches MERIDIAN_V3_TEST_DB to its own isolated sim DB
    mid-run and must restore the caller's original context afterward — the
    calling test's own `session` fixture must still be a valid, usable
    SQLAlchemy session once run_backtest() returns, not bound to a disposed
    engine from the sim DB swap."""
    import os

    from meridian_v3.storage.schema import WatchItem

    before_env = os.environ.get("MERIDIAN_V3_TEST_DB")
    start, end = _seed_trend(session, "TESTSYM", days=30)

    run_backtest(["TESTSYM"], start=start, end=end, starting_capital=50_000.0)

    assert os.environ.get("MERIDIAN_V3_TEST_DB") == before_env  # restored, not left pointed at the sim DB

    # The original session must still work: query it, and prove a write
    # actually reaches the *original* database, not a stale/disposed one.
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    session.add(WatchItem(symbol="POSTCHECK", asset_class="equity", status="active", created_at=now, updated_at=now))
    session.flush()
    row = session.query(WatchItem).filter_by(symbol="POSTCHECK").one()
    assert row.symbol == "POSTCHECK"

    # And the HistoricalBar rows seeded before the backtest are still there.
    assert session.query(HistoricalBar).filter_by(symbol="TESTSYM").count() > 0


def test_run_backtest_raises_a_clear_error_with_no_historical_data(session):
    import pytest

    with pytest.raises(ValueError, match="No HistoricalBar data"):
        run_backtest(["NODATA"], start=date(2026, 1, 1), end=date(2026, 3, 1))


def test_run_backtest_does_not_leak_across_two_isolated_runs(session):
    """Two sequential backtests must each get their own isolated DB and
    starting capital — not accumulate positions/equity from a prior run."""
    start, end = _seed_trend(session, "TESTSYM", days=60)

    result_a = run_backtest(["TESTSYM"], start=start, end=end, starting_capital=50_000.0)
    result_b = run_backtest(["TESTSYM"], start=start, end=end, starting_capital=50_000.0)

    assert result_a.db_path != result_b.db_path
    # Same input data, same starting capital -> deterministic, identical outcome.
    assert result_a.final_equity == result_b.final_equity
    assert result_a.closed_trades == result_b.closed_trades


def test_chunked_backtest_handles_an_empty_universe_without_spawning():
    """Guard the degenerate case: no symbols must return cleanly rather than
    starting a process pool with nothing to do."""
    from datetime import date

    from meridian_v3.backtest.engine import run_backtest_chunked

    out = run_backtest_chunked([], start=date(2026, 1, 1), end=date(2026, 2, 1))
    assert out["trades"] == 0
    assert out["pnls"] == []
    assert out["chunks"] == 0
