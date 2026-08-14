"""Indian futures and options the engine may pick on paper.

Full NSE lots are too big for a ₹50,000 book. India futures use a
paper mini-lot. Options are buying only.
"""

from __future__ import annotations

INDIA_DERIV_UNIVERSE: tuple[tuple[str, str, str], ...] = (
    ("NIFTY.F", "future", "Nifty mini future"),
    ("BANKNIFTY.F", "future", "Bank Nifty mini future"),
    ("SENSEX.F", "future", "Sensex mini future"),
    ("RELIANCE.F", "future", "Reliance stock future — mini"),
    ("HDFCBANK.F", "future", "HDFC Bank stock future — mini"),
    ("INFY.F", "future", "Infosys stock future — mini"),
    ("NIFTY.C", "option", "Nifty call — buy only"),
    ("BANKNIFTY.C", "option", "Bank Nifty call — buy only"),
    ("RELIANCE.C", "option", "Reliance call — buy only"),
)
