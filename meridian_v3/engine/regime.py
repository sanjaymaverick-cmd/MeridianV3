"""Simple market regime detection.

Two pictures live side by side:

1. Desk mood (from V1): Calm / Elevated / Stress
2. Tape shape (V3): Trending / Mean-reverting + High-vol / Low-vol

Trending books prefer breakouts. Mean-reverting books prefer fades.
High-vol books shrink size. Low-vol books can take the normal size.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

from meridian_v3.config import RegimeCfg
from meridian_v3.domain.models import RegimeLabel, TapeRegime


@dataclass
class RegimeInputs:
    vix: float | None = None
    ewma_vol_ann: float | None = None
    kalman_trend_z: float | None = None
    nifty_vs_sma50: float | None = None
    hurst: float | None = None
    atr_pct: float | None = None


@dataclass(frozen=True)
class MarketRegime:
    desk: str
    tape: str
    vol: str
    reason: str
    risk_mult: float


def hurst_proxy(closes: Sequence[float]) -> float | None:
    """Very small Hurst-like score in (0, 1). >0.55 trend, <0.45 mean-revert."""
    if len(closes) < 20:
        return None
    rets = [closes[i] / closes[i - 1] - 1.0 for i in range(1, len(closes)) if closes[i - 1]]
    if len(rets) < 16:
        return None
    def _rs(window: list[float]) -> float:
        mean = sum(window) / len(window)
        dev = 0.0
        path = []
        run = 0.0
        for r in window:
            run += r - mean
            path.append(run)
        rng = max(path) - min(path)
        var = sum((r - mean) ** 2 for r in window) / len(window)
        std = var ** 0.5
        return rng / std if std > 1e-12 else 0.0
    half = len(rets) // 2
    rs_full = _rs(rets)
    rs_half = (_rs(rets[:half]) + _rs(rets[half:])) / 2
    if rs_half <= 1e-12:
        return 0.5
    h = (abs(rs_full) / rs_half).bit_length() if False else _log_ratio(rs_full, rs_half)
    return max(0.2, min(0.8, h))


def _log_ratio(a: float, b: float) -> float:
    import math

    return 0.5 + 0.15 * math.log(max(a, 1e-9) / max(b, 1e-9))


def detect_tape(inputs: RegimeInputs) -> tuple[str, str]:
    h = inputs.hurst
    if h is not None and h >= 0.55:
        tape = TapeRegime.TRENDING.value
    elif h is not None and h <= 0.45:
        tape = TapeRegime.MEAN_REVERTING.value
    elif inputs.kalman_trend_z is not None and abs(inputs.kalman_trend_z) >= 1.0:
        tape = TapeRegime.TRENDING.value
    else:
        tape = TapeRegime.MEAN_REVERTING.value
    vol = TapeRegime.HIGH_VOL.value
    atr = inputs.atr_pct
    ewma = inputs.ewma_vol_ann
    if (atr is not None and atr < 0.012) or (ewma is not None and ewma < 0.14):
        vol = TapeRegime.LOW_VOL.value
    if (atr is not None and atr >= 0.025) or (ewma is not None and ewma >= 0.22):
        vol = TapeRegime.HIGH_VOL.value
    return tape, vol


def detect_desk(inputs: RegimeInputs, cfg: RegimeCfg | None = None) -> str:
    cfg = cfg or RegimeCfg()
    rank = 0
    if inputs.vix is not None:
        if inputs.vix >= cfg.vix_elevated_max:
            rank = 2
        elif inputs.vix >= cfg.vix_calm_max:
            rank = 1
    if inputs.ewma_vol_ann is not None:
        if inputs.ewma_vol_ann >= cfg.ewma_vol_stress:
            rank = max(rank, 2)
        elif inputs.ewma_vol_ann >= cfg.ewma_vol_elevated:
            rank = max(rank, 1)
    labels = (RegimeLabel.CALM.value, RegimeLabel.ELEVATED.value, RegimeLabel.STRESS.value)
    return labels[rank]


def combine(inputs: RegimeInputs, cfg: RegimeCfg | None = None) -> MarketRegime:
    desk = detect_desk(inputs, cfg)
    tape, vol = detect_tape(inputs)
    risk = 1.0
    if desk == RegimeLabel.STRESS.value or vol == TapeRegime.HIGH_VOL.value:
        risk = 0.55
    elif desk == RegimeLabel.ELEVATED.value:
        risk = 0.80
    reason = f"{desk} desk · {tape.replace('_', ' ')} tape · {vol.replace('_', ' ')}"
    return MarketRegime(desk=desk, tape=tape, vol=vol, reason=reason, risk_mult=risk)
