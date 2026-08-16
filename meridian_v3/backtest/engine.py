"""The backtest core: replays the *real* live pipeline day by day against
``HistoricalBar`` data, on a fully isolated database.

Deliberately reuses the exact functions the live desk runs, rather than a
parallel reimplementation that could silently drift from what live actually
does:

  - ``data_providers.service._apply_frame`` — builds ``PriceCache``/
    ``PriceBar`` from an OHLC frame, exactly as a live refresh would, just
    fed a causal (no-lookahead) historical window instead of a live fetch.
  - ``autopilot.manage_exits`` — the same stop/target/tape-flip exit logic
    a live tick runs.
  - ``pipeline.run_cycle`` — the same ``decide()``, OMS, fee calculation,
    belief/logit training, and (per Part 3) walk-forward robustness gate a
    live cycle runs.
  - ``pipeline.mark_to_market`` — the same equity/peak accounting.

The isolated database means this can never touch the live paper book —
same mechanism the test suite already uses (``MERIDIAN_V3_TEST_DB``), just
driven by a real CLI run instead of pytest.
"""

from __future__ import annotations

import os
import tempfile
import uuid
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path

import pandas as pd
from sqlalchemy import select

from meridian_v3.config import get_settings, reset_settings_cache
from meridian_v3.storage.db import get_session, init_db, reset_engine
from meridian_v3.storage.schema import AccountState, HistoricalBar, WatchItem

# A bounded lookback fed into _apply_frame each simulated day — generously
# past the live pipeline's own longest window (sma50, 14-period ATR, and
# the ta-features module's ~28-bar ADX quirk found in signals/features.py),
# without re-scanning an ever-growing history every single day.
_LOOKBACK_BARS = 300

# 10:00 IST -> the NSE session is open, so india_session()/market_session()
# checks inside run_cycle() behave the same way they would on a real
# trading day, rather than treating every simulated day as after-hours.
_SESSION_HOUR_UTC = 4
_SESSION_MINUTE_UTC = 30


@dataclass
class BacktestResult:
    symbols: list[str]
    start: date
    end: date
    starting_capital: float
    final_equity: float
    peak_equity: float
    max_drawdown_pct: float
    trading_days_simulated: int
    equity_curve: list[tuple[date, float]] = field(default_factory=list)
    closed_trades: int = 0
    wins: int = 0
    losses: int = 0
    db_path: str = ""
    # Time-ordered realized P&L of every closed clip, for walk-forward
    # scoring (pipeline.seed_robustness_from_backtest).
    closed_pnls: list[float] = field(default_factory=list)

    @property
    def win_rate(self) -> float:
        total = self.wins + self.losses
        return self.wins / total if total else 0.0

    @property
    def total_return_pct(self) -> float:
        if self.starting_capital <= 0:
            return 0.0
        return (self.final_equity - self.starting_capital) / self.starting_capital


def _load_historical_bars(symbols: list[str], start: date, end: date) -> dict[str, list[HistoricalBar]]:
    """Read HistoricalBar rows from the *real* desk DB, once, up front —
    the day-by-day walk below never touches that database again, so there's
    no dual-session complexity while the isolated sim DB is active."""
    session = get_session()
    try:
        out: dict[str, list[HistoricalBar]] = {}
        for symbol in symbols:
            rows = list(
                session.scalars(
                    select(HistoricalBar)
                    .where(HistoricalBar.symbol == symbol, HistoricalBar.bar_date >= start, HistoricalBar.bar_date <= end)
                    .order_by(HistoricalBar.bar_date.asc())
                )
            )
            if rows:
                out[symbol] = rows
        return out
    finally:
        session.close()


def _hist_frame(bars: list[HistoricalBar]) -> pd.DataFrame:
    idx = pd.DatetimeIndex([pd.Timestamp(b.bar_date) for b in bars])
    return pd.DataFrame(
        {"Open": [b.open for b in bars], "High": [b.high for b in bars], "Low": [b.low for b in bars],
         "Close": [b.close for b in bars], "Volume": [b.volume for b in bars]},
        index=idx,
    )


def run_backtest(
    symbols: list[str],
    *,
    start: date,
    end: date,
    starting_capital: float = 50_000.0,
) -> BacktestResult:
    """Walk ``symbols`` forward day by day from ``start`` to ``end``,
    replaying the real decision/execution/training pipeline on an isolated
    database. Returns a summary; the isolated DB file is left on disk (its
    path is in the result) so you can inspect it further if you want to.
    """
    from meridian_v3.autopilot import manage_exits
    from meridian_v3.data_providers.service import _apply_frame
    from meridian_v3.pipeline import mark_to_market, run_cycle

    bars_by_symbol = _load_historical_bars(symbols, start, end)
    if not bars_by_symbol:
        raise ValueError(f"No HistoricalBar data for {symbols} in [{start}, {end}] — run backfill-history first.")

    trading_days = sorted({b.bar_date for bars in bars_by_symbol.values() for b in bars})

    # --- Switch to an isolated database for the whole simulation ---------
    original_test_db = os.environ.get("MERIDIAN_V3_TEST_DB")
    tmp_dir = Path(tempfile.gettempdir()) / "meridian_v3_backtests"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    # A uuid suffix, not just a timestamp: two backtests starting within the
    # same second (sequential fast runs, or parallel pytest workers) would
    # otherwise collide on one file and the second would fail seeding
    # account_state against a DB that already has it. CI caught this as a
    # flaky failure -- one runner hit the same-second case, another didn't.
    db_path = tmp_dir / f"backtest_{datetime.now(timezone.utc):%Y%m%d_%H%M%S}_{uuid.uuid4().hex[:8]}.sqlite"
    try:
        os.environ["MERIDIAN_V3_TEST_DB"] = str(db_path)
        reset_settings_cache()
        reset_engine()
        settings = get_settings()
        settings.test_db = str(db_path)
        init_db()
        session = get_session()

        now0 = datetime.now(timezone.utc).replace(tzinfo=None)
        session.add(
            AccountState(
                venue="paper", cash=starting_capital, equity=starting_capital, peak=starting_capital,
                paper_auto=1, updated_at=now0,
            )
        )
        session.add(AccountState(venue="live", cash=50_000.0, equity=50_000.0, peak=50_000.0, updated_at=now0))
        for symbol in bars_by_symbol:
            session.add(
                WatchItem(symbol=symbol, asset_class="equity", status="active", created_at=now0, updated_at=now0)
            )
        session.commit()

        equity_curve: list[tuple[date, float]] = []
        peak_equity = starting_capital
        max_dd = 0.0

        for day in trading_days:
            sim_now = datetime(day.year, day.month, day.day, _SESSION_HOUR_UTC, _SESSION_MINUTE_UTC)

            for symbol, bars in bars_by_symbol.items():
                causal = [b for b in bars if b.bar_date <= day][-_LOOKBACK_BARS:]
                if len(causal) < 5:
                    continue
                _apply_frame(session, symbol, _hist_frame(causal), now=sim_now)

            manage_exits(session, in_session=True, minutes_to_close=999, now=sim_now)
            run_cycle(session, now=sim_now, live_armed=False)
            equity = mark_to_market(session, "paper")
            session.flush()

            peak_equity = max(peak_equity, equity)
            dd = (peak_equity - equity) / peak_equity if peak_equity > 0 else 0.0
            max_dd = max(max_dd, dd)
            equity_curve.append((day, equity))

        session.commit()

        from meridian_v3.storage.schema import Position

        closed = list(
            session.scalars(
                select(Position)
                .where(Position.venue == "paper", Position.status == "closed")
                .order_by(Position.closed_at.asc())
            )
        )
        wins = sum(1 for p in closed if (p.realized_pnl or 0) > 0)
        losses = sum(1 for p in closed if (p.realized_pnl or 0) < 0)
        final_equity = equity_curve[-1][1] if equity_curve else starting_capital

        result = BacktestResult(
            symbols=list(bars_by_symbol.keys()),
            start=start,
            end=end,
            starting_capital=starting_capital,
            final_equity=final_equity,
            peak_equity=peak_equity,
            max_drawdown_pct=max_dd,
            trading_days_simulated=len(trading_days),
            equity_curve=equity_curve,
            closed_trades=len(closed),
            wins=wins,
            losses=losses,
            db_path=str(db_path),
            closed_pnls=[float(p.realized_pnl) for p in closed if p.realized_pnl is not None],
        )
        session.close()
        return result
    finally:
        # Restore whatever the caller's DB context was before this ran —
        # a backtest must never leave the process pointed at a temp file.
        if original_test_db is None:
            os.environ.pop("MERIDIAN_V3_TEST_DB", None)
        else:
            os.environ["MERIDIAN_V3_TEST_DB"] = original_test_db
        reset_settings_cache()
        reset_engine()
