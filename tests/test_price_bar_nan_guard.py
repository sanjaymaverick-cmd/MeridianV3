"""A provider can hand back a bar with a real Open/High/Low but a NaN Close
(a genuine upstream data gap — this is what broke CI: a real yfinance fetch
for USDCHF returned exactly this shape). ``price_bars.close`` is NOT NULL,
so ``_apply_frame`` must skip the bad row instead of crashing the whole
refresh over one bar."""

from __future__ import annotations

import math
from datetime import datetime, timezone

import pandas as pd

from meridian_v3.data_providers.service import _apply_frame
from meridian_v3.storage.schema import PriceBar


def _now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def test_a_bar_with_nan_close_is_skipped_not_a_crash(session):
    idx = pd.date_range("2026-08-01", periods=6, freq="D")
    hist = pd.DataFrame(
        {
            "Open": [100.0, 101.0, 102.0, 103.0, 117.38, 105.0],
            "High": [101.0, 102.0, 103.0, 104.0, 117.38, 106.0],
            "Low": [99.0, 100.0, 101.0, 102.0, 117.38, 104.0],
            # Row index 4 mirrors the real USDCHF fetch that broke CI:
            # real Open/High/Low, NaN Close.
            "Close": [100.5, 101.5, 102.5, 103.5, math.nan, 105.5],
            "Volume": [1000.0] * 6,
        },
        index=idx,
    )
    ok = _apply_frame(session, "USDCHF", hist, _now())
    assert ok is True

    rows = session.query(PriceBar).filter_by(symbol="USDCHF").all()
    assert len(rows) == 5  # the NaN-close bar never reached the table
    assert all(r.close == r.close for r in rows)  # no NaN ever landed


def test_all_bars_nan_close_still_returns_false_not_a_crash(session):
    idx = pd.date_range("2026-08-01", periods=6, freq="D")
    hist = pd.DataFrame(
        {
            "Open": [100.0] * 6,
            "High": [101.0] * 6,
            "Low": [99.0] * 6,
            "Close": [math.nan] * 6,
            "Volume": [1000.0] * 6,
        },
        index=idx,
    )
    ok = _apply_frame(session, "ALLBAD", hist, _now())
    assert ok is False  # too few real closes to mark the cache (< 5)
    rows = session.query(PriceBar).filter_by(symbol="ALLBAD").all()
    assert rows == []
