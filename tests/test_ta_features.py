"""signals/features.py — the ta-library feature set. Additive: nothing in
the live pipeline reads this yet, it's for the backtester's training
features and future signal-engine experiments."""

from __future__ import annotations

import math

from meridian_v3.signals.features import compute_ta_features


def _trend(n=40, start=100.0, step=1.0):
    closes = [start + i * step for i in range(n)]
    highs = [c + 1 for c in closes]
    lows = [c - 1 for c in closes]
    volumes = [1000.0] * n
    return highs, lows, closes, volumes


def test_too_few_bars_returns_all_none_not_a_crash():
    highs, lows, closes, volumes = _trend(n=3)
    out = compute_ta_features(highs, lows, closes, volumes)
    assert out["rsi"] is None
    assert out["adx"] is None
    assert out["bb_pband"] is None


def test_a_steady_uptrend_produces_a_high_rsi():
    highs, lows, closes, volumes = _trend(n=40, step=1.0)
    out = compute_ta_features(highs, lows, closes, volumes)
    assert out["rsi"] is not None
    assert out["rsi"] > 60  # a clean, steady uptrend should read overbought-ish
    assert out["macd_diff"] is not None
    assert out["roc"] is not None
    assert out["roc"] > 0  # positive rate of change on a rising series


def test_partial_lookback_windows_populate_progressively():
    """atr/stoch/williams_r/rsi need >=14 bars; bollinger needs >=20; adx
    needs roughly 2x its 14-bar window (confirmed empirically against the
    installed ta version, not documented) — a 15-bar series gets the first
    group but not bollinger or adx yet."""
    highs, lows, closes, volumes = _trend(n=15)
    out = compute_ta_features(highs, lows, closes, volumes)
    assert out["atr"] is not None
    assert out["rsi"] is not None
    assert out["adx"] is None  # short of adx's real ~28-bar requirement
    assert out["bb_pband"] is None  # still short of the 20-bar window


def test_adx_populates_once_past_its_real_lookback_requirement():
    """ADXIndicator needs ~2x its window (28 bars for the default 14) before
    it stops raising internally — this locks in the _safe() wrapper handles
    that instead of crashing, and that adx does eventually populate."""
    highs, lows, closes, volumes = _trend(n=30)
    out = compute_ta_features(highs, lows, closes, volumes)
    assert out["adx"] is not None


def test_no_nan_ever_leaks_out_as_a_value():
    highs, lows, closes, volumes = _trend(n=25)
    out = compute_ta_features(highs, lows, closes, volumes)
    for key, val in out.items():
        assert val is None or (isinstance(val, float) and val == val), f"{key} leaked a NaN"


def test_missing_volume_defaults_cleanly():
    highs, lows, closes, _ = _trend(n=25)
    out = compute_ta_features(highs, lows, closes, None)
    assert out["rsi"] is not None  # doesn't crash without volume
