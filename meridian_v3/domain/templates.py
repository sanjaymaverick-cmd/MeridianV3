"""Complete simple-language review catalog.

Every template is a finished shape the desk can fill. Numbers and math
are already computed before this file runs. Each review title contains
“(not an order)”. Tone is calm and plain — a 10-year-old and a
60-year-old should both understand it.
"""

from __future__ import annotations

CATALOG: dict[str, dict[str, str]] = {
    "balanced": {
        "title": "Full Greeks review (not an order)",
        "plain": (
            "The book is in a normal mix of leftover direction, bend, vol-sensitivity, "
            "and time. Read all four numbers. Review only if something feels off."
        ),
    },
    "long_gamma_theta_cost": {
        "title": "Long gamma, paying time (not an order)",
        "plain": (
            "You are paying a little every quiet day so that a market jump can help. "
            "That trade-off is the point of long gamma. Keep leftover direction small."
        ),
    },
    "short_gamma_theta_benefit": {
        "title": "Short gamma, collecting time (not an order)",
        "plain": (
            "Time is paying you while the tape is quiet. A jump can take that back. "
            "This is not a harvest and not an edge."
        ),
    },
    "short_gamma_negative_theta": {
        "title": "Short gamma and paying time (not an order)",
        "plain": (
            "Time and a jump can both hurt. This is the high-risk corner. "
            "Review a cut or an opposite option before the next gap."
        ),
    },
    "over_limit_vega": {
        "title": "Vega is over the line (not an order)",
        "plain": (
            "Vol-sensitivity is larger than the line you set. A vol jump would move "
            "the book more than you planned. Review a cut or an opposite option."
        ),
    },
    "quiet_market": {
        "title": "Quiet tape — time is the main story (not an order)",
        "plain": (
            "The tape is quiet, so time is doing most of the work. "
            "Gamma scalping is small next to Daily PnL today."
        ),
    },
    "residual_delta": {
        "title": "Leftover delta drift (not an order)",
        "plain": (
            "Direction is leftover. A small futures clip can flatten it. "
            "This is a review, not an order."
        ),
    },
    "combined": {
        "title": "Delta, gamma, vega and theta together (not an order)",
        "plain": (
            "Read leftover direction, bend, vol-sensitivity, and time as one picture. "
            "One number never tells the whole story."
        ),
    },
    "gamma_scalp": {
        "title": "Gamma scalping status (not an order)",
        "plain": (
            "Long gamma: sell the rise and buy the dip on the hedge — that can help. "
            "Short gamma: the same loop runs backward and can hurt."
        ),
    },
    "vega_action": {
        "title": "Vega action to review (not an order)",
        "plain": (
            "Each way to move vega also moves delta, gamma, and theta. "
            "Read those four side-effects before you accept."
        ),
    },
    "auto_decision": {
        "title": "Auto decision review (not an order)",
        "plain": (
            "The engine scored this name and wrote a paper trade. "
            "A live clip is only considered if confidence is very high "
            "and the safety lights are green."
        ),
    },
    "drawdown_pause": {
        "title": "Live trading is paused (not an order)",
        "plain": (
            "The dedicated account has fallen 20% from its peak. "
            "Open positions may stay open. New live trades wait until the book heals."
        ),
    },
    "imported_book": {
        "title": "Imported holding review (not an order)",
        "plain": (
            "This name came from a statement you imported. "
            "The risk engine now watches it the same way it watches a new trade."
        ),
    },
}

CHOICES = [
    "Accept and write an intended-trade note",
    "Dismiss this review",
    "Snooze and look again later",
    "Do nothing and keep watching",
]

STRUCTURE = (
    "title with “(not an order)”",
    "Delta / Gamma / Vega / Theta in simple words + numbers",
    "Daily PnL line",
    "Gamma Scalping PnL line",
    "Model suggestion (size and direction, review only)",
    "Choices the person can take",
)

VEGA_ACTION_KEYS = (
    ("limit_cap", "Stay under the vega line"),
    ("cut_options", "Shrink the option itself"),
    ("hedge_options", "Add an opposite option"),
    ("book_balance", "Balance vega across the book"),
    ("regime_limit", "Tighten the line for this market mood"),
    ("time_reduce", "Let vega fall as expiry gets close"),
)


def catalog_payload() -> dict:
    return {
        "structure": list(STRUCTURE),
        "choices": list(CHOICES),
        "scenarios": [
            {"key": key, "title": item["title"], "plain": item["plain"]}
            for key, item in CATALOG.items()
        ],
        "vega_actions": [{"key": key, "title": title} for key, title in VEGA_ACTION_KEYS],
        "note": "The frontend never receives model coefficients. Only these finished words and numbers.",
    }
