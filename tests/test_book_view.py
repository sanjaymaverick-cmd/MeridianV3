from datetime import datetime, timezone

import pytest

from meridian_v3.storage.schema import Position
from meridian_v3.ui.book_view import decorate_positions, summarize_closed


def test_open_row_shows_avg_current_and_pnl(session):
    from meridian_v3.storage.schema import PriceCache

    session.add(PriceCache(symbol="INFY", last=1600, quality="live"))
    session.add(
        Position(
            venue="paper",
            market="equity_cash",
            symbol="INFY",
            side="buy",
            qty=2,
            avg_price=1400,
            status="open",
            source="test",
            opened_at=datetime.now(timezone.utc).replace(tzinfo=None),
        )
    )
    session.flush()
    cards = decorate_positions(session, list(session.query(Position)))
    assert cards[0]["avg_buy"] == 1400
    assert cards[0]["current"] == 1600
    assert cards[0]["pnl"] == 400
    assert cards[0]["pnl_class"] == "up"


def test_settled_row_keeps_profit(session):
    session.add(
        Position(
            venue="paper",
            market="equity_cash",
            symbol="TCS",
            side="buy",
            qty=0,
            avg_price=3000,
            close_qty=1,
            exit_price=3200,
            realized_pnl=200,
            status="closed",
            source="test",
            opened_at=datetime.now(timezone.utc).replace(tzinfo=None),
        )
    )
    session.flush()
    cards = decorate_positions(session, list(session.query(Position)))
    assert cards[0]["avg_buy"] == 3000
    assert cards[0]["current"] == 3200
    assert cards[0]["gross"] == 200
    assert cards[0]["pnl"] == 200
    assert cards[0]["pnl_class"] == "up"
    summary = summarize_closed(cards)
    assert summary["count"] == 1
    assert summary["wins"] == 1
    assert summary["pnl"] == 200


def test_decorate_moves_silver_decimal_and_shows_real_pnl(session):
    """₹622.87 vs ₹6,216.29 is a one-place slide. Lined up, the short is a small win."""
    session.add(
        Position(
            venue="paper",
            market="global_commodities",
            symbol="SILVER.X",
            side="sell",
            qty=0,
            avg_price=622.87,
            close_qty=0.04,
            exit_price=6216.29,
            realized_pnl=-223.83,
            status="closed",
            source="test",
            opened_at=datetime.now(timezone.utc).replace(tzinfo=None),
        )
    )
    session.flush()
    card = decorate_positions(session, list(session.query(Position)))[0]
    assert card["avg_buy"] == pytest.approx(6228.7, abs=0.1)
    assert card["current"] == pytest.approx(6216.29, abs=0.1)
    # Sold 6,228.70, covered 6,216.29 → about +₹0.50 on the tape.
    assert card["gross"] == pytest.approx(0.50, abs=0.05)
    assert card["pnl"] == pytest.approx(0.50, abs=0.05)
    assert card["pnl"] > 0


def test_flat_silver_clip_tape_is_zero_net_is_the_bill(session):
    from meridian_v3.storage.schema import Fill

    opened = datetime.now(timezone.utc).replace(tzinfo=None)
    session.add(
        Position(
            venue="paper",
            market="global_commodities",
            symbol="SILVER.X",
            side="sell",
            qty=0,
            avg_price=6219.63,
            close_qty=0.04,
            exit_price=6219.63,
            realized_pnl=-0.09,
            status="closed",
            source="test",
            opened_at=opened,
            closed_at=opened,
        )
    )
    session.add(
        Fill(
            venue="paper",
            symbol="SILVER.X",
            side="sell",
            qty=0.04,
            price=6219.63,
            fees=0.01,
            filled_at=opened,
        )
    )
    session.add(
        Fill(
            venue="paper",
            symbol="SILVER.X",
            side="buy",
            qty=0.04,
            price=6219.63,
            fees=0.08,
            filled_at=opened,
        )
    )
    session.flush()
    card = decorate_positions(session, list(session.query(Position)))[0]
    assert card["gross"] == pytest.approx(0.0, abs=0.001)
    assert card["fees"] == pytest.approx(0.09, abs=0.001)
    assert card["pnl"] == pytest.approx(-0.09, abs=0.001)
    assert card["gross"] != card["fees"]
    assert card["pnl"] != card["fees"]
