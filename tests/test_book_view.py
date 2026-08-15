from datetime import datetime, timezone

import pytest

from meridian_v3.storage.schema import Fill, Position, PriceCache
from meridian_v3.ui.book_view import decorate_fills, decorate_positions, summarize_closed


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
    # 2.A.4 — same-mark, fee-only settle: a small negative net P&L must not
    # be colored like a real directional loss.
    assert card["pnl_class"] == "scratch"


def test_settled_fee_only_scratch_is_not_colored_like_a_loss(session):
    """A closed clip with gross ≈ 0 (fees-only net) gets the neutral 'scratch' tone.

    decorate_positions recomputes gross/net live from avg/exit price and the
    matching Fill fees (not from the stored `realized_pnl`), so the fee needs
    to come from real Fill rows for `pnl` to land negative here.
    """
    opened = datetime.now(timezone.utc).replace(tzinfo=None)
    session.add(
        Position(
            venue="paper",
            market="equity_cash",
            symbol="INFY",
            side="buy",
            qty=0,
            avg_price=1600.0,
            close_qty=2,
            exit_price=1600.1,  # 0.1 * 2 = 0.20 tape move, well under the 0.5 epsilon
            realized_pnl=-4.8,
            status="closed",
            source="test",
            opened_at=opened,
            closed_at=opened,
        )
    )
    session.add(
        Fill(venue="paper", symbol="INFY", side="buy", qty=2, price=1600.0, fees=2.5, filled_at=opened)
    )
    session.add(
        Fill(venue="paper", symbol="INFY", side="sell", qty=2, price=1600.1, fees=2.5, filled_at=opened)
    )
    session.flush()
    card = decorate_positions(session, list(session.query(Position)))[0]
    assert abs(card["gross"]) <= 0.5
    assert card["pnl"] < 0
    assert card["pnl_class"] == "scratch"


def test_settled_real_loss_still_reads_down(session):
    """Positive control: a large directional loss must still get the 'down' tone."""
    opened = datetime.now(timezone.utc).replace(tzinfo=None)
    session.add(
        Position(
            venue="paper",
            market="equity_cash",
            symbol="INFY",
            side="buy",
            qty=0,
            avg_price=1600.0,
            close_qty=2,
            exit_price=1550.0,  # real 50-point drop
            realized_pnl=-105.0,
            status="closed",
            source="test",
            opened_at=opened,
            closed_at=opened,
        )
    )
    session.flush()
    card = decorate_positions(session, list(session.query(Position)))[0]
    assert card["gross"] < -0.5
    assert card["pnl_class"] == "down"


def test_unreconciled_position_surfaces_its_status(session):
    """2.A.3 — a repair-flagged unreconciled row must be visible, not dropped."""
    opened = datetime.now(timezone.utc).replace(tzinfo=None)
    session.add(
        Position(
            venue="paper",
            market="equity_cash",
            symbol="WIPRO",
            side="buy",
            qty=0,
            avg_price=400.0,
            close_qty=1,
            exit_price=None,
            realized_pnl=None,
            status="unreconciled",
            source="test",
            opened_at=opened,
            closed_at=opened,
        )
    )
    session.flush()
    card = decorate_positions(session, list(session.query(Position)))[0]
    assert card["status"] == "unreconciled"
    assert card["unreconciled"] is True


def test_fx_fallback_quality_surfaces_on_the_card(session):
    """2.A.3 — a stale FX-fallback mark (Phase 2) must be visible on the card."""
    session.add(PriceCache(symbol="SILVER.X", last=6200.0, quality="fx_fallback"))
    session.add(
        Position(
            venue="paper",
            market="global_commodities",
            symbol="SILVER.X",
            side="buy",
            qty=1,
            avg_price=6100.0,
            status="open",
            source="test",
            opened_at=datetime.now(timezone.utc).replace(tzinfo=None),
        )
    )
    session.flush()
    card = decorate_positions(session, list(session.query(Position)))[0]
    assert card["price_quality"] == "fx_fallback"


def test_live_quality_mark_is_not_flagged(session):
    """2.A.3 regression — an ordinary "live" quality must not be treated as
    a flag. `PriceCache.quality` is set to "live" by every real refresh
    (never the literal "ok"), so checking "!= ok" would wrongly badge nearly
    every position on the book; only "fx_fallback" is a real flag today."""
    session.add(PriceCache(symbol="INFY", last=1500.0, quality="live"))
    session.add(
        Position(
            venue="paper",
            market="equity_cash",
            symbol="INFY",
            side="buy",
            qty=1,
            avg_price=1490.0,
            status="open",
            source="test",
            opened_at=datetime.now(timezone.utc).replace(tzinfo=None),
        )
    )
    session.flush()
    card = decorate_positions(session, list(session.query(Position)))[0]
    assert card["price_quality"] is None


def test_decorate_fills_carries_correction_note(session):
    """2.A.3 — a repair-corrected fill's correction_note must reach the UI card
    while the original `note` (the append-only ticket) stays untouched."""
    session.add(
        Fill(
            venue="paper",
            symbol="GOLD.X",
            side="buy",
            qty=1,
            price=62864.0,
            fees=12.0,
            filled_at=datetime.now(timezone.utc).replace(tzinfo=None),
            note="BUY GOLD.X @ 6286.40",
            correction_note="Repair: price corrected from ₹6,286.40 to ₹62,864.00 (x10 decimal-slide fix).",
        )
    )
    session.flush()
    cards = decorate_fills(list(session.query(Fill)))
    assert cards[0]["note"] == "BUY GOLD.X @ 6286.40"
    assert cards[0]["correction_note"]
    assert "corrected" in cards[0]["correction_note"].lower()
