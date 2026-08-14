"""Algo-owned NSE / BSE scan universe. Not a user watchlist."""

from meridian_v3.universe.crypto import BINANCE_UNIVERSE
from meridian_v3.universe.derivatives import INDIA_DERIV_UNIVERSE
from meridian_v3.universe.nse_bse import ALGO_UNIVERSE, install_universe, universe_symbols

__all__ = [
    "ALGO_UNIVERSE",
    "BINANCE_UNIVERSE",
    "INDIA_DERIV_UNIVERSE",
    "install_universe",
    "universe_symbols",
]
