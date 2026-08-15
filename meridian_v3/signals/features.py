"""A richer, optional feature set on top of the hand-rolled RSI/ATR that
already drive the live decision pipeline (``engine/meta_label.py``,
``engine/atr.py``) — additive, not a replacement. Meant for the backtester's
model-training feature set and for future signal-engine experiments; nothing
in the live pipeline calls this yet.

Wraps a curated subset of the ``ta`` library's 40+ indicators rather than
``add_all_ta_features`` (43 columns of mostly-correlated noise per bar is a
worse training signal than a deliberately chosen handful).
"""

from __future__ import annotations

from typing import Sequence

import pandas as pd


def compute_ta_features(
    highs: Sequence[float], lows: Sequence[float], closes: Sequence[float], volumes: Sequence[float] | None = None
) -> dict[str, float | None]:
    """Latest-bar value for a curated set of momentum/trend/volatility
    indicators. Returns ``None`` for any indicator that can't be computed
    yet (too few bars for its lookback window) rather than a misleading 0.0
    — callers should treat ``None`` the same way ``factor_parts_from_tape``
    already treats a missing factor: skip it, don't fake it.
    """
    n = len(closes)
    if n < 5:
        return _empty_features()

    close = pd.Series(closes, dtype=float)
    high = pd.Series(highs, dtype=float)
    low = pd.Series(lows, dtype=float)
    vol = pd.Series(volumes, dtype=float) if volumes else pd.Series([0.0] * n)

    from ta.momentum import ROCIndicator, RSIIndicator, StochasticOscillator, WilliamsRIndicator
    from ta.trend import ADXIndicator, MACD
    from ta.volatility import AverageTrueRange, BollingerBands

    def _last(series: pd.Series) -> float | None:
        if series.empty:
            return None
        val = series.iloc[-1]
        return float(val) if val == val else None  # NaN guard, same idiom as data_providers/service.py

    def _safe(compute) -> float | None:
        # Several ta indicators (ADXIndicator in particular — confirmed by
        # testing, not documented — needs roughly 2x its window before it
        # stops raising IndexError instead of returning NaN) can throw on
        # too little history rather than degrade gracefully. Treat any such
        # failure the same as "not enough data yet": None, not a crash.
        try:
            return _last(compute())
        except Exception:
            return None

    out: dict[str, float | None] = {}
    out["rsi"] = _safe(lambda: RSIIndicator(close=close).rsi())

    macd = MACD(close=close)
    out["macd"] = _safe(macd.macd)
    out["macd_signal"] = _safe(macd.macd_signal)
    out["macd_diff"] = _safe(macd.macd_diff)

    out["adx"] = _safe(lambda: ADXIndicator(high=high, low=low, close=close).adx())
    out["atr"] = _safe(lambda: AverageTrueRange(high=high, low=low, close=close).average_true_range())
    stoch = StochasticOscillator(high=high, low=low, close=close)
    out["stoch"] = _safe(stoch.stoch)
    out["stoch_signal"] = _safe(stoch.stoch_signal)
    out["williams_r"] = _safe(lambda: WilliamsRIndicator(high=high, low=low, close=close).williams_r())

    bb = BollingerBands(close=close, window=20, window_dev=2)
    out["bb_pband"] = _safe(bb.bollinger_pband)  # 0=at lower band, 1=at upper band
    out["bb_width"] = _safe(bb.bollinger_wband)

    out["roc"] = _safe(lambda: ROCIndicator(close=close).roc())

    return out


def _empty_features() -> dict[str, float | None]:
    return {
        "rsi": None, "macd": None, "macd_signal": None, "macd_diff": None,
        "adx": None, "atr": None, "stoch": None, "stoch_signal": None,
        "williams_r": None, "bb_pband": None, "bb_width": None, "roc": None,
    }
