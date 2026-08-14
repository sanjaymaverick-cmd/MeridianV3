"""Multi-market router.

Equity Cash is home. Capital can visit F&O (options buying only) or
Forex (nano/micro) when those tapes are clearly stronger. It prefers
to walk back home when the visit is over.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Route:
    market: str
    score: float
    reason: str


def route_market(
    *,
    equity_score: float,
    options_score: float,
    forex_score: float,
    options_allowed: bool = True,
    forex_allowed: bool = True,
    home_bonus: float = 8.0,
    shift_margin: float = 12.0,
) -> Route:
    equity = equity_score + home_bonus
    options = options_score if options_allowed else -1e9
    forex = forex_score if forex_allowed else -1e9
    ranked = [
        ("equity_cash", equity, "Equity cash is home. We stay here unless another tape is clearly stronger."),
        ("options_buy", options, "Options buying only. Premium must be small enough for the ₹5,000 book."),
        ("forex_micro", forex, "Forex nano/micro only. A visit, not a new home."),
    ]
    ranked.sort(key=lambda item: item[1], reverse=True)
    best_name, best_score, best_why = ranked[0]
    home_score = equity
    if best_name != "equity_cash" and best_score < home_score + shift_margin:
        return Route("equity_cash", home_score, "The visitor is not stronger enough. Capital stays in equity cash.")
    if best_name == "equity_cash":
        return Route(best_name, best_score, best_why)
    return Route(
        best_name,
        best_score,
        f"{best_why} Shift is allowed because this tape beats home by {best_score - home_score:.0f} points.",
    )
