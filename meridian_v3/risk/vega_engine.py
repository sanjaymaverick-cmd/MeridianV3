"""Vega defense primitives from V2. Math stays here."""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date
from enum import Enum
from typing import Optional, Sequence


class PolicyKind(str, Enum):
    INVENTORY_HEDGE = "inventory_hedge"
    VOL_HARVEST = "vol_harvest"
    VEGA_DEFENSE = "vega_defense"


@dataclass(frozen=True)
class OptionGreekLeg:
    leg_id: str
    symbol: str
    contract_label: str
    lots: float
    multiplier: float
    mark_inr: float
    delta: float
    gamma: float
    vega_per_lot: float
    theta_per_lot: float = 0.0
    iv: Optional[float] = None
    as_of: Optional[date] = None
    stale: bool = False

    @property
    def vega(self) -> float:
        return self.lots * self.vega_per_lot

    @property
    def effective_delta_lots(self) -> float:
        return self.lots * self.delta

    @property
    def gamma_lots(self) -> float:
        return self.lots * self.gamma


@dataclass(frozen=True)
class VegaPolicy:
    symbol: str
    vega_limit: float
    warn_utilization: float = 0.80
    nu_star: float = 0.0
    enabled: bool = True
    hedge_vega_per_lot: Optional[float] = None
    hedge_delta: float = 0.5
    lot_step: float = 1.0


def utilization(net_vega: float, limit: float) -> float:
    if limit <= 0:
        return 0.0
    return abs(net_vega) / limit


def snap_lots(raw: float, lot_step: float) -> float:
    if lot_step <= 0:
        return raw
    return math.copysign(math.floor(abs(raw) / lot_step + 1e-12) * lot_step, raw)
