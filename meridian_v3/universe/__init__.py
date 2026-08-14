"""Algo-owned scan universe. Not a user watchlist."""

from meridian_v3.universe.crypto import BINANCE_UNIVERSE
from meridian_v3.universe.derivatives import INDIA_DERIV_UNIVERSE
from meridian_v3.universe.global_markets import COMMODITY_UNIVERSE, FX_UNIVERSE
from meridian_v3.universe.nse_bse import ALGO_UNIVERSE, install_universe, universe_symbols

__all__ = [
    "ALGO_UNIVERSE",
    "BINANCE_UNIVERSE",
    "COMMODITY_UNIVERSE",
    "FX_UNIVERSE",
    "INDIA_DERIV_UNIVERSE",
    "install_universe",
    "universe_symbols",
]
