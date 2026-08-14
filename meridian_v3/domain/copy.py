"""Binding UI copy rules.

Reviews stay review-only. Live order notes are a separate stream and
must never be mixed into a review card.
"""

from __future__ import annotations

REVIEW_FORBIDDEN = (
    "you should hedge",
    "must reduce",
    "buy usd",
    "must hedge",
    "place order",
    "auto-send",
    "live order sent",
)


def assert_review_copy(text: str) -> str:
    lowered = text.lower()
    if "(not an order)" not in lowered:
        raise ValueError("every review must say “(not an order)”")
    for phrase in REVIEW_FORBIDDEN:
        if phrase in lowered:
            raise ValueError(f"forbidden execution language in a review: {phrase!r}")
    return text


def fx_review_copy(*, pair: str, move_pct: float, regime: str, why: str) -> str:
    direction = "up" if move_pct >= 0 else "down"
    text = (
        f"FX REVIEW — not an order. {pair} is {direction} {abs(move_pct):.2f}% "
        f"({regime}). {why} Review whether the current hedge still matches the book, "
        f"or note the residual and wait?"
    )
    return assert_review_copy(text)


def live_note(*, symbol: str, side: str, qty: float, price: float, venue: str) -> str:
    """Clear live/paper fill language. This is not a review."""
    label = "LIVE FILL" if venue == "live" else "PAPER FILL"
    return (
        f"{label}: {side.upper()} {qty:g} {symbol} near ₹{price:,.2f}. "
        "This is a recorded fill on the dedicated account, not a review card."
    )
