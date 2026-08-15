"""data_providers/historical.py — the multi-year backfill feeding the
backtester.

yfinance is the PRIMARY source (verified empirically against a real
corporate action — RELIANCE's 2024-10-28 bonus — that its Close column is
split/bonus-adjusted even with auto_adjust=False; jugaad-data's raw NSE
bhavcopy is not, and shows a ~50% fake overnight gap on the same date).
jugaad-data is only a fallback for symbols yfinance has nothing for.

Both `_fetch_yfinance`/`_fetch_jugaad_fallback` hit real networks, so every
test here monkeypatches them — CI must not be flaky because a provider
rate-limited a GitHub Actions IP."""

from __future__ import annotations

from datetime import date

import pandas as pd

from meridian_v3.data_providers import historical as hist_module
from meridian_v3.data_providers.historical import backfill_nse_symbol, backfill_nse_watchlist
from meridian_v3.storage.schema import HistoricalBar, WatchItem


def _yf_df(rows: list[tuple]) -> pd.DataFrame:
    """rows: (date, open, high, low, close, volume) — yfinance's own column
    naming and DatetimeIndex shape."""
    idx = pd.DatetimeIndex([pd.Timestamp(r[0]) for r in rows])
    return pd.DataFrame(
        {"Open": [r[1] for r in rows], "High": [r[2] for r in rows], "Low": [r[3] for r in rows],
         "Close": [r[4] for r in rows], "Volume": [r[5] for r in rows]},
        index=idx,
    )


def _jugaad_df(rows: list[tuple]) -> pd.DataFrame:
    return pd.DataFrame(
        [{"DATE": pd.Timestamp(d), "OPEN": o, "HIGH": h, "LOW": l, "CLOSE": c, "VOLUME": v} for d, o, h, l, c, v in rows]
    )


def test_backfill_uses_yfinance_as_the_primary_source(session, monkeypatch):
    """The whole point of the redesign: yfinance (adjusted) wins whenever
    it has data at all — jugaad-data's fallback must not even be called."""
    yf_df = _yf_df([(date(2026, 1, 2), 100, 102, 99, 101, 5000), (date(2026, 1, 3), 101, 103, 100, 102, 6000)])
    monkeypatch.setattr(hist_module, "_fetch_yfinance", lambda symbol, years: yf_df)

    called_jugaad = []
    monkeypatch.setattr(hist_module, "_fetch_jugaad_fallback", lambda symbol, years: called_jugaad.append(1) or None)

    n = backfill_nse_symbol(session, "RELIANCE", years=1)
    session.flush()
    assert n == 2
    assert called_jugaad == []  # fallback never invoked when yfinance succeeds
    rows = session.query(HistoricalBar).filter_by(symbol="RELIANCE").all()
    assert all(r.source == "yfinance" for r in rows)


def test_backfill_writes_historical_bars_not_price_bars(session, monkeypatch):
    """The whole point of a separate table: HistoricalBar, never PriceBar."""
    from meridian_v3.storage.schema import PriceBar

    yf_df = _yf_df([(date(2026, 1, 2), 100, 102, 99, 101, 5000)])
    monkeypatch.setattr(hist_module, "_fetch_yfinance", lambda symbol, years: yf_df)

    n = backfill_nse_symbol(session, "RELIANCE", years=1)
    session.flush()
    assert n == 1
    assert session.query(HistoricalBar).filter_by(symbol="RELIANCE").count() == 1
    assert session.query(PriceBar).filter_by(symbol="RELIANCE").count() == 0


def test_backfill_falls_back_to_jugaad_when_yfinance_has_nothing(session, monkeypatch):
    """A symbol yfinance can't find at all must still get an honest attempt
    via NSE-direct data, source-tagged as unadjusted so a consumer can tell."""
    monkeypatch.setattr(hist_module, "_fetch_yfinance", lambda symbol, years: None)
    jg_df = _jugaad_df([(date(2026, 1, 2), 100, 102, 99, 101, 5000)])
    monkeypatch.setattr(hist_module, "_fetch_jugaad_fallback", lambda symbol, years: jg_df)

    n = backfill_nse_symbol(session, "OBSCURESYM", years=1)
    session.flush()
    assert n == 1
    row = session.query(HistoricalBar).filter_by(symbol="OBSCURESYM").one()
    assert row.source == "jugaad_nse_unadjusted"  # tagged, so a consumer knows it's not adjusted


def test_backfill_is_idempotent_on_rerun(session, monkeypatch):
    yf_df = _yf_df([(date(2026, 1, 2), 100, 102, 99, 101, 5000)])
    monkeypatch.setattr(hist_module, "_fetch_yfinance", lambda symbol, years: yf_df)

    backfill_nse_symbol(session, "RELIANCE", years=1)
    session.flush()
    assert session.query(HistoricalBar).filter_by(symbol="RELIANCE").count() == 1

    # Re-running with an updated close for the same bar_date must update the
    # existing row in place, not insert a duplicate.
    yf_df2 = _yf_df([(date(2026, 1, 2), 100, 102, 99, 105, 5000)])
    monkeypatch.setattr(hist_module, "_fetch_yfinance", lambda symbol, years: yf_df2)
    backfill_nse_symbol(session, "RELIANCE", years=1)
    session.flush()

    rows = session.query(HistoricalBar).filter_by(symbol="RELIANCE").all()
    assert len(rows) == 1
    assert rows[0].close == 105


def test_backfill_skips_zero_price_holiday_rows(session, monkeypatch):
    yf_df = _yf_df(
        [
            (date(2026, 1, 2), 100, 102, 99, 101, 5000),
            (date(2026, 1, 3), 0, 0, 0, 0, 0),  # a no-trade/holiday artifact row
        ]
    )
    monkeypatch.setattr(hist_module, "_fetch_yfinance", lambda symbol, years: yf_df)

    n = backfill_nse_symbol(session, "RELIANCE", years=1)
    session.flush()
    assert n == 1
    assert session.query(HistoricalBar).filter_by(symbol="RELIANCE").count() == 1


def test_backfill_dedupes_a_same_date_row_within_one_fetch(session, monkeypatch):
    """A real bug hit running this against live NSE data: a long (5-year)
    jugaad-data range came back with two rows for the same date (plausibly
    a pagination-boundary overlap on their end) — the second one tripped
    the (symbol, bar_date) unique constraint on INSERT because `existing`
    only reflected rows already committed *before* this call, not ones
    this same loop had already added. Still relevant now that jugaad-data
    is the fallback path, not just when it was primary."""
    monkeypatch.setattr(hist_module, "_fetch_yfinance", lambda symbol, years: None)
    jg_df = _jugaad_df(
        [
            (date(2026, 6, 23), 1300, 1305, 1298, 1302, 500000),
            (date(2026, 6, 23), 1300, 1305, 1298, 1309.5, 719579),  # duplicate date, later/adjusted values
        ]
    )
    monkeypatch.setattr(hist_module, "_fetch_jugaad_fallback", lambda symbol, years: jg_df)

    n = backfill_nse_symbol(session, "RELIANCE", years=5)
    session.flush()  # would raise IntegrityError before the fix
    assert n == 2
    rows = session.query(HistoricalBar).filter_by(symbol="RELIANCE").all()
    assert len(rows) == 1  # one row survives, not a constraint violation
    assert rows[0].close == 1309.5  # the later occurrence in the fetch wins


def test_backfill_both_sources_failing_returns_zero_not_a_crash(session, monkeypatch):
    monkeypatch.setattr(hist_module, "_fetch_yfinance", lambda symbol, years: None)
    monkeypatch.setattr(hist_module, "_fetch_jugaad_fallback", lambda symbol, years: None)
    assert backfill_nse_symbol(session, "NOSUCHSYMBOL", years=1) == 0


def test_backfill_watchlist_only_targets_active_equity_symbols(session, monkeypatch):
    """Crypto/FX/commodity watch items must never reach this pipeline (wrong
    market entirely) — only status="active", asset_class="equity" rows."""
    from datetime import datetime, timezone

    now = datetime.now(timezone.utc).replace(tzinfo=None)
    session.add_all(
        [
            WatchItem(symbol="RELIANCE", asset_class="equity", status="active", created_at=now, updated_at=now),
            WatchItem(symbol="BTCUSDT", asset_class="crypto", status="active", created_at=now, updated_at=now),
            WatchItem(symbol="RETIRED", asset_class="equity", status="inactive", created_at=now, updated_at=now),
        ]
    )
    session.flush()

    calls: list[str] = []

    def _spy(symbol, years):
        calls.append(symbol)
        return _yf_df([(date(2026, 1, 2), 100, 102, 99, 101, 5000)])

    monkeypatch.setattr(hist_module, "_fetch_yfinance", _spy)
    result = backfill_nse_watchlist(session, years=1, sleep_ms=0)

    assert calls == ["RELIANCE"]
    assert result["symbols"] == 1
    assert result["ok"] == 1
