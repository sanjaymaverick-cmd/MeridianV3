from meridian_v3.storage.schema import AccountState, WatchItem
from meridian_v3.storage.seed import seed_demo
from meridian_v3.universe import ALGO_UNIVERSE, universe_symbols


def test_universe_covers_nse_and_bse():
    names = universe_symbols()
    assert "RELIANCE" in names
    assert "INFY" in names
    assert "SBIN" in names
    assert any(row[1] == "NSE" for row in ALGO_UNIVERSE)
    assert any(row[1] == "BSE" for row in ALGO_UNIVERSE)
    assert len(names) >= 50


def test_seed_installs_universe_and_fifty_thousand(session):
    seed_demo(session, reset=True)
    symbols = {row.symbol for row in session.query(WatchItem).all()}
    assert "RELIANCE" in symbols
    assert "ICICIBANK" in symbols
    assert "DIVISLAB" in symbols
    assert "GOLD.X" in symbols
    assert "EURUSD" in symbols
    assert "BTCUSDT" in symbols
    assert len(symbols) >= 50
    paper = session.query(AccountState).filter_by(venue="paper").one()
    assert paper.cash == 50_000
    assert paper.equity == 50_000


def test_seed_lifts_old_five_thousand_book(session):
    seed_demo(session, reset=True)
    paper = session.query(AccountState).filter_by(venue="paper").one()
    paper.cash = 5000
    paper.equity = 5000
    paper.peak = 5000
    session.flush()
    seed_demo(session)
    paper = session.query(AccountState).filter_by(venue="paper").one()
    assert paper.cash == 50_000
    assert paper.equity == 50_000


def test_seed_credits_paper_even_with_open_clips(session):
    from datetime import datetime, timezone

    from meridian_v3.storage.schema import Position

    seed_demo(session, reset=True)
    paper = session.query(AccountState).filter_by(venue="paper").one()
    paper.cash = 500
    paper.equity = 4990
    paper.peak = 5000
    session.add(
        Position(
            venue="paper",
            market="equity_cash",
            symbol="INFY",
            side="buy",
            qty=1,
            avg_price=1400,
            stop=1300,
            horizon="intraday",
            status="open",
            source="test",
            opened_at=datetime.now(timezone.utc).replace(tzinfo=None),
        )
    )
    session.flush()
    seed_demo(session)
    paper = session.query(AccountState).filter_by(venue="paper").one()
    assert abs(paper.cash - 45_500) < 0.01
    assert abs(paper.equity - 49_990) < 0.01
    assert paper.peak == 50_000
