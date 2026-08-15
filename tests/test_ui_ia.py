"""Part 2, 2.C/2.D — information architecture: symbol picker, pagination
cursors for Signals/Fills, and the by-symbol churn-tally grouping."""

import re
from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient

from meridian_v3.storage.schema import Fill, SignalRow, WatchItem


def _now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def test_signals_pagination_before_cursor_returns_older_non_overlapping_page(session):
    """2.C.11 — `?before=<id>` must walk strictly further back, not repeat
    or skip rows. 55 seeded signals, 50 per page -> page two has exactly the
    5 oldest and no further "Load more"."""
    base = _now()
    for i in range(55):
        session.add(
            SignalRow(
                symbol="INFY",
                side="buy",
                confidence=0.6,
                confluence=2,
                market="equity_cash",
                paper=1,
                live=0,
                reason="test signal",
                created_at=base + timedelta(seconds=i),
            )
        )
    session.commit()
    from meridian_v3.app import create_app

    client = TestClient(create_app())
    first = client.get("/signals")
    assert first.status_code == 200
    assert b"Load more decisions" in first.content

    match = re.search(rb"/signals\?before=(\d+)", first.content)
    assert match, "expected a Load more decisions link with a before= cursor"
    cursor = int(match.group(1))

    second = client.get(f"/signals?before={cursor}")
    assert second.status_code == 200
    assert b"Load more decisions" not in second.content

    # 55 seeded rows, 50 shown on page one -> exactly 5 remain on page two.
    assert second.content.count(b"test signal") == 5


def test_fills_pagination_before_cursor_bounded_query(session):
    """2.C.11 — Fills used to be an unbounded `select(Fill)` sliced to 40 in
    Python. 45 seeded fills, 40 per page -> page two has exactly the 5
    oldest and no further "Load more"."""
    base = _now()
    for i in range(45):
        session.add(
            Fill(
                venue="paper",
                symbol="TCS",
                side="buy",
                qty=1,
                price=100.0 + i,
                fees=1.0,
                filled_at=base + timedelta(seconds=i),
                note=f"BUY TCS #{i}",
            )
        )
    session.commit()
    from meridian_v3.app import create_app

    client = TestClient(create_app())
    first = client.get("/book")
    assert first.status_code == 200
    assert b"Load more fills" in first.content
    assert first.content.count(b"BUY TCS #") == 40

    match = re.search(rb"/book\?before=(\d+)", first.content)
    assert match, "expected a Load more fills link with a before= cursor"
    cursor = int(match.group(1))

    second = client.get(f"/book?before={cursor}")
    assert second.status_code == 200
    assert b"Load more fills" not in second.content
    assert second.content.count(b"BUY TCS #") == 5


def test_grouped_signals_by_symbol_counts_paper_held_live_and_top_side(session):
    """2.C.14 — the churn-tally grouping (Finding F16) must correctly count
    decisions, paper-executed vs. held, live, and the most common side, per
    symbol, and sort by count descending."""
    from meridian_v3.ui.routes import _group_signals_by_symbol

    base = _now()
    for i, (side, paper) in enumerate([("buy", 1), ("buy", 1), ("sell", 0)]):
        session.add(
            SignalRow(
                symbol="INFY",
                side=side,
                confidence=0.5,
                confluence=1,
                market="equity_cash",
                paper=paper,
                live=0,
                reason="infy decision",
                created_at=base + timedelta(seconds=i),
            )
        )
    session.add(
        SignalRow(
            symbol="TCS",
            side="buy",
            confidence=0.5,
            confluence=1,
            market="equity_cash",
            paper=1,
            live=1,
            reason="tcs decision",
            created_at=base + timedelta(seconds=10),
        )
    )
    session.commit()

    grouped = _group_signals_by_symbol(session)
    by_symbol = {g["symbol"]: g for g in grouped}

    assert by_symbol["INFY"]["count"] == 3
    assert by_symbol["INFY"]["paper"] == 2
    assert by_symbol["INFY"]["held"] == 1
    assert by_symbol["INFY"]["live"] == 0
    assert by_symbol["INFY"]["top_side"] == "buy"

    assert by_symbol["TCS"]["count"] == 1
    assert by_symbol["TCS"]["paper"] == 1
    assert by_symbol["TCS"]["live"] == 1

    # sorted by count descending: INFY (3) ahead of TCS (1)
    assert grouped[0]["symbol"] == "INFY"


def test_signals_page_renders_the_grouped_panel(session):
    session.add(
        SignalRow(
            symbol="WIPRO",
            side="buy",
            confidence=0.5,
            confluence=1,
            market="equity_cash",
            paper=1,
            live=0,
            reason="wipro reason",
            created_at=_now(),
        )
    )
    session.commit()
    from meridian_v3.app import create_app

    client = TestClient(create_app())
    res = client.get("/signals")
    assert res.status_code == 200
    assert b"By symbol" in res.content
    assert b"WIPRO" in res.content


def test_active_watch_symbols_feed_chart_and_review_datalists(session):
    """2.C.9 — the symbol-picker datalist must offer only *active* watch
    symbols (matching the filter pipeline.run_cycle itself uses), not every
    symbol ever added to the watchlist."""
    session.add(
        WatchItem(
            symbol="ZOMATO", asset_class="equity", status="active",
            notes="", created_at=_now(), updated_at=_now(),
        )
    )
    session.add(
        WatchItem(
            symbol="RETIRED", asset_class="equity", status="inactive",
            notes="", created_at=_now(), updated_at=_now(),
        )
    )
    session.commit()
    from meridian_v3.app import create_app

    client = TestClient(create_app())
    chart = client.get("/chart")
    assert chart.status_code == 200
    assert b'value="ZOMATO"' in chart.content
    assert b'value="RETIRED"' not in chart.content

    review = client.get("/review")
    assert review.status_code == 200
    assert b'value="ZOMATO"' in review.content
    assert b'value="RETIRED"' not in review.content


def test_review_page_accepts_symbol_query_param(session):
    """2.C.9 — /review used to hardcode symbol="NIFTY" with no way to view
    any other name from the page itself."""
    from meridian_v3.app import create_app

    client = TestClient(create_app())
    res = client.get("/review?symbol=infy")
    assert res.status_code == 200
    assert b"INFY" in res.content
