"""1.4 — round-trip reconstruction must track real position state.

The old adjacent-pair walk in ``build_meta_labels.py`` had no notion of
position state: it just looked at fills two-at-a-time. For an interleaved
short sequence like [sell, buy, sell, buy] (open short, cover, open short
again, cover again) it would still "work" by luck of alternation, but the
audit (F7) traced a case where it mispairs a cover-buy with a *later*,
unrelated open-sell. These tests build fills where naive adjacency and
correct position-state tracking would disagree, and assert the fixed
stack-based walk always pairs a closing fill with the lot it actually closes.
"""

from __future__ import annotations

import sys
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from build_meta_labels import reconstruct_honest_roundtrips  # noqa: E402


def _fill(fid, symbol, side, qty, price, minute, fees=1.0):
    return {
        "id": fid,
        "symbol": symbol,
        "side": side,
        "qty": qty,
        "price": price,
        "fees": fees,
        "filled_at": datetime(2026, 1, 1, 9, 0) + timedelta(minutes=minute),
    }


def test_interleaved_shorts_pair_the_right_cover():
    """[sell, buy, sell, buy] — two back-to-back shorts on one symbol.

    Fill 1 opens short #1 at 100. Fill 2 covers short #1 at 90 (a win).
    Fill 3 opens short #2 at 95. Fill 4 covers short #2 at 110 (a loss).
    A naive adjacent-pair walk with no position-state tracking is exactly
    the shape the audit flagged as able to mispair fill 2's cover with
    fill 3's unrelated open-sell. The fixed stack-based walk must instead
    close each cover against the short it actually belongs to.
    """
    fills = pd.DataFrame(
        [
            _fill(1, "NIFTY.F", "sell", 10, 100.0, 0),   # open short #1
            _fill(2, "NIFTY.F", "buy", 10, 90.0, 5),      # cover short #1 (win)
            _fill(3, "NIFTY.F", "sell", 10, 95.0, 10),    # open short #2
            _fill(4, "NIFTY.F", "buy", 10, 110.0, 15),    # cover short #2 (loss)
        ]
    )
    rt = reconstruct_honest_roundtrips(fills)

    assert len(rt) == 2
    assert set(rt["direction"]) == {"short"}

    first = rt[(rt["buy_fill_id"] == 2)].iloc[0]
    assert first["sell_fill_id"] == 1, "cover-buy #2 must close open-sell #1, not #3"
    assert first["honest_pnl"] > 0  # sold at 100, covered at 90 — a real win

    second = rt[(rt["buy_fill_id"] == 4)].iloc[0]
    assert second["sell_fill_id"] == 3, "cover-buy #4 must close open-sell #3, not an unrelated fill"
    assert second["honest_pnl"] < 0  # sold at 95, covered at 110 — a real loss

    # No cover-buy is ever paired with a fill from a *different* short.
    assert not ((rt["buy_fill_id"] == 2) & (rt["sell_fill_id"] == 3)).any()


def test_interleaved_short_then_long_do_not_cross_pair():
    """[sell, buy, buy, sell] — a short fully closes, then a fresh long opens.

    Fill 2's cover-buy exactly flattens fill 1's short. Fill 3 is then a
    brand-new long entry (nothing left open to add to), closed by fill 4.
    The two round trips must not blend fills from the short into the long.
    """
    fills = pd.DataFrame(
        [
            _fill(1, "INFY", "sell", 5, 1500.0, 0),   # open short
            _fill(2, "INFY", "buy", 5, 1480.0, 5),     # cover — flattens the short
            _fill(3, "INFY", "buy", 5, 1490.0, 10),    # opens a fresh long
            _fill(4, "INFY", "sell", 5, 1510.0, 15),   # closes the long
        ]
    )
    rt = reconstruct_honest_roundtrips(fills).sort_values("buy_time").reset_index(drop=True)

    assert len(rt) == 2
    short_row = rt[rt["direction"] == "short"].iloc[0]
    long_row = rt[rt["direction"] == "long"].iloc[0]

    assert short_row["sell_fill_id"] == 1 and short_row["buy_fill_id"] == 2
    assert long_row["buy_fill_id"] == 3 and long_row["sell_fill_id"] == 4
    # The long's entry is fill 3, never fill 1 (the unrelated short's open).
    assert long_row["buy_time"] != fills.loc[0, "filled_at"]


def test_same_side_adds_stack_instead_of_dropping_the_first_fill():
    """[buy, buy, sell] — two adds to a long, then one full close.

    The old naive walk only paired an immediately-adjacent (buy, sell) and
    silently dropped a buy that wasn't followed straight away by a sell.
    The stack must instead close the most recent lot first (LIFO) and keep
    accounting for the earlier one rather than losing it.
    """
    fills = pd.DataFrame(
        [
            _fill(1, "TCS", "buy", 3, 3000.0, 0),
            _fill(2, "TCS", "buy", 3, 3050.0, 5),
            _fill(3, "TCS", "sell", 3, 3100.0, 10),
        ]
    )
    rt = reconstruct_honest_roundtrips(fills)
    # Only the second (more recent) buy lot is closed by the single sell —
    # the first buy lot (fill 1) is still open, so exactly one round trip.
    assert len(rt) == 1
    row = rt.iloc[0]
    assert row["buy_fill_id"] == 2
    assert row["sell_fill_id"] == 3
    assert row["direction"] == "long"


def test_empty_fills_returns_empty_frame():
    rt = reconstruct_honest_roundtrips(pd.DataFrame(columns=["symbol", "side", "qty", "price", "fees", "filled_at", "id"]))
    assert rt.empty
