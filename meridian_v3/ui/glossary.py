"""Plain-English explanations for every piece of desk jargon.

One source of truth, server-side, so the same wording is testable and can't
drift between pages. The UI ships this to the browser as JSON and
``meridian-v3.js`` attaches a tooltip to any term it finds.

Writing rules for entries here:
  * Explain it to someone who has never traded. No jargon inside the
    definition of the jargon.
  * Say what it means for *this* book, in rupees or plain consequence,
    not the textbook definition.
  * One or two sentences. A tooltip nobody finishes reading is decoration.
"""

from __future__ import annotations

# term -> (short label, plain-English body)
GLOSSARY: dict[str, tuple[str, str]] = {
    # --- money and the book -------------------------------------------------
    "equity": (
        "Equity",
        "What the whole book is worth right now: cash plus the current value of everything held.",
    ),
    "cash": (
        "Cash",
        "Money not tied up in any position — what's available to open something new.",
    ),
    "peak": (
        "Peak",
        "The highest the book has ever been worth. Used as the reference point for measuring a slump.",
    ),
    "drawdown": (
        "Drawdown",
        "How far the book has fallen from its best-ever value, as a percentage. "
        "At 8% the desk starts trading smaller; at 20% it stops opening new positions entirely.",
    ),
    "clip": (
        "Clip",
        "One trade. Desk slang for a single position the book has opened.",
    ),
    "fill": (
        "Fill",
        "A trade that actually went through, at a specific price and quantity.",
    ),
    "position": (
        "Position",
        "Something the book currently holds and hasn't sold yet.",
    ),
    "venue": (
        "Venue",
        "Whether a trade is pretend money (paper) or real money (live).",
    ),
    "paper": (
        "Paper",
        "Pretend money. Trades are recorded and scored exactly like real ones, but nothing is actually bought.",
    ),
    "live": (
        "Live",
        "Real money at a real broker. Stays switched off until you deliberately arm it.",
    ),
    "armed": (
        "Armed",
        "The safety catch is off and real-money orders are permitted. Off by default.",
    ),
    "scratch": (
        "Scratch",
        "A trade closed at roughly the price it opened at. The only thing lost was fees — not a real win or loss.",
    ),
    "flatten": (
        "Flatten",
        "Close a position and go back to holding cash.",
    ),

    # --- the decision -------------------------------------------------------
    "confidence": (
        "Confidence",
        "How sure the desk is about this specific trade, 0-100. Higher confidence is allowed a bigger position.",
    ),
    "confluence": (
        "Confluence",
        "How many separate signals agree with each other. One indicator saying 'buy' is weak; five agreeing is strong.",
    ),
    "edge": (
        "Edge",
        "The money this trade is expected to make on average, after subtracting what it costs to place. "
        "If that number isn't positive, the desk doesn't trade.",
    ),
    "meta-label": (
        "Meta-label",
        "A second opinion. The first model picks a direction; this one judges whether that call is worth acting on.",
    ),
    "freshness": (
        "Freshness",
        "How recent the signal is. An old signal is discounted, because the reason for it may have passed.",
    ),
    "horizon": (
        "Horizon",
        "How long a trade is meant to be held: intraday (closed same day) or positional (held for days).",
    ),
    "intraday": (
        "Intraday",
        "Opened and closed within the same trading day.",
    ),
    "positional": (
        "Positional",
        "Held for several days, through overnight gaps.",
    ),
    "hold": (
        "Hold",
        "The desk looked at this and decided to do nothing. Usually the right answer.",
    ),

    # --- risk ---------------------------------------------------------------
    "atr": (
        "ATR",
        "Average True Range — how much this thing typically moves in a day, in rupees. "
        "Used to size stops so a quiet stock and a wild one are treated differently.",
    ),
    "stop": (
        "Stop",
        "The price at which a losing trade is cut. Set before entering, so the loss is decided in advance.",
    ),
    "target": (
        "Target",
        "The price at which a winning trade is taken. Also set before entering.",
    ),
    "kelly": (
        "Kelly fraction",
        "A formula for how much to bet given your odds. The desk deliberately uses a fraction of it, "
        "because full Kelly is famously too aggressive to live through.",
    ),
    "risk": (
        "Risk",
        "The most this trade can lose if the stop is hit — not the amount invested.",
    ),
    "kill switch": (
        "Kill switch",
        "A hard rupee limit on losses in a single day. Once hit, the desk stops trading until tomorrow.",
    ),
    "regime": (
        "Regime",
        "The market's current mood — calm, elevated, or stressed. The desk trades smaller when it's stressed.",
    ),
    "vix": (
        "VIX",
        "The market's fear gauge. High VIX means traders expect big swings.",
    ),

    # --- costs --------------------------------------------------------------
    "round trip": (
        "Round trip",
        "The full cost of getting in and back out again. Both halves are charged, so it's roughly double "
        "the cost of a single order.",
    ),
    "tds": (
        "TDS",
        "A 1% Indian tax on every crypto transfer. Charged on the way in AND the way out, which is why a "
        "crypto trade must move over 2% just to break even.",
    ),
    "stt": (
        "STT",
        "Securities Transaction Tax — a government charge on Indian share trades. Crypto and currency don't pay it.",
    ),
    "gst": (
        "GST",
        "18% tax charged on the broker's fee (not on the trade itself).",
    ),
    "brokerage": (
        "Brokerage",
        "What the broker charges to place the order.",
    ),
    "slippage": (
        "Slippage",
        "The gap between the price you expected and the price you actually got. Worse on thinly traded things.",
    ),
    "spread": (
        "Spread",
        "The difference between the buying price and the selling price. You pay half of it on the way in and "
        "half on the way out.",
    ),

    # --- validation ---------------------------------------------------------
    "walk-forward": (
        "Walk-forward",
        "Testing a strategy by training it on older data and checking it on newer data it has never seen — "
        "repeatedly, moving forward through history. Catches strategies that only worked in hindsight.",
    ),
    "robustness": (
        "Robustness",
        "Whether the strategy still worked on data it wasn't trained on. If not, the desk trades smaller.",
    ),
    "out-of-sample": (
        "Out-of-sample",
        "Data the model has never seen. The only honest test of whether something actually works.",
    ),
    "backtest": (
        "Backtest",
        "Replaying the strategy against real historical prices to see what it would have done.",
    ),
    "belief": (
        "Belief",
        "The desk's running track record for each market — how often it has been right there so far. "
        "It updates after every closed trade.",
    ),

    # --- markets ------------------------------------------------------------
    "equity_cash": ("Equity cash", "Ordinary Indian shares, bought outright. Open 09:15-15:30 IST on weekdays."),
    "crypto_spot": ("Crypto spot", "Coins bought outright on Binance. The only market open 24/7, weekends included."),
    "global_commodities": ("Commodities", "Gold, silver, oil, wheat and similar, traded on global exchanges."),
    "forex_micro": ("Forex", "Currency pairs, traded in very small sizes. Closed over the weekend."),
    "india_futures": ("India futures", "Contracts to buy or sell an Indian index later, at a price agreed now."),
    "options_buy": ("Options", "The right — not the obligation — to buy or sell later. This desk only ever buys them."),

    # --- indicators ---------------------------------------------------------
    "rsi": ("RSI", "A 0-100 gauge of whether something has been bought or sold too hard recently."),
    "sma": ("Moving average", "The average price over recent days, which smooths out the daily noise to show direction."),
    "macd": ("MACD", "Compares a fast and a slow average to spot momentum turning."),
    "adx": ("ADX", "Measures how strong a trend is — not which way it's going."),
    "bollinger": ("Bollinger bands", "A channel drawn around the average price. Touching the edge means an unusually big move."),
    "breakout": ("Breakout", "Price pushing past its recent ceiling or floor, often the start of a bigger move."),
    "mean reversion": ("Mean reversion", "The tendency of an unusually large move to snap back toward the average."),

    # --- greeks -------------------------------------------------------------
    "delta": ("Delta", "How much an option's price moves when the underlying moves ₹1."),
    "gamma": ("Gamma", "How fast delta itself changes. High gamma means the position's behaviour shifts quickly."),
    "vega": ("Vega", "How much an option's price moves when the market gets more or less jumpy."),
}


def glossary_payload() -> dict[str, dict[str, str]]:
    """The glossary shaped for the browser."""
    return {term: {"label": label, "body": body} for term, (label, body) in GLOSSARY.items()}
