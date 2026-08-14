from datetime import datetime, timezone

from meridian_v3.storage.schema import Position
from meridian_v3.ui.book_view import decorate_positions


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
    assert cards[0]["pnl"] == 200
    assert cards[0]["pnl_class"] == "up"
