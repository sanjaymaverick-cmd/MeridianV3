"""2.5 — live enforcement of a re-entry cooldown per symbol (F16).

``reentry_sec`` used to only exist as a post-hoc training feature in
``build_meta_labels.py``; nothing stopped ``run_cycle`` from reopening the
same symbol on back-to-back cycles the moment its last clip closed, paying
brokerage + GST again each time. ``_reentry_blocked`` (pipeline.py) is the
live gate: hold unless the cooldown has elapsed or the new signal is
materially more confident than the one that closed the prior clip.
"""

from datetime import datetime, timedelta, timezone

import pytest

from meridian_v3.config import get_settings
from meridian_v3.execution.brokers.paper_broker import PaperBroker
from meridian_v3.execution.oms import OrderManager
from meridian_v3.pipeline import _reentry_blocked
from meridian_v3.storage.schema import AccountState, Position


def _now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _paper(session, cash=50_000.0):
    session.add(AccountState(venue="paper", cash=cash, equity=cash, peak=cash, updated_at=_now()))


def _closed_position(session, symbol, *, closed_at, opened_confidence):
    session.add(
        Position(
            venue="paper",
            market="equity_cash",
            symbol=symbol,
            side="buy",
            qty=0,
            avg_price=100.0,
            close_qty=1,
            exit_price=105.0,
            realized_pnl=5.0,
            status="closed",
            source="test",
            opened_at=closed_at,
            closed_at=closed_at,
            opened_confidence=opened_confidence,
        )
    )


def test_no_prior_close_is_never_blocked(session):
    settings = get_settings()
    assert _reentry_blocked(session, "NOPRIOR", 0.5, _now(), settings) is None


def test_recent_close_with_similar_confidence_is_blocked(session):
    _paper(session)
    now = _now()
    _closed_position(session, "INFY", closed_at=now - timedelta(seconds=10), opened_confidence=0.60)
    session.flush()
    settings = get_settings()
    reason = _reentry_blocked(session, "INFY", 0.62, now, settings)
    assert reason is not None
    assert "cooldown" in reason.lower()
    assert "INFY" in reason


def test_materially_higher_confidence_overrides_the_cooldown(session):
    _paper(session)
    now = _now()
    _closed_position(session, "INFY", closed_at=now - timedelta(seconds=10), opened_confidence=0.60)
    session.flush()
    settings = get_settings()
    # 0.60 + reentry_confidence_margin (0.05 default) = 0.65 is the bar.
    assert _reentry_blocked(session, "INFY", 0.90, now, settings) is None


def test_cooldown_elapses_on_its_own(session):
    _paper(session)
    now = _now()
    settings = get_settings()
    long_ago = now - timedelta(seconds=settings.decision.reentry_cooldown_sec + 5)
    _closed_position(session, "INFY", closed_at=long_ago, opened_confidence=0.90)
    session.flush()
    # Same confidence as before, but the cooldown window has fully elapsed.
    assert _reentry_blocked(session, "INFY", 0.90, now, settings) is None


def test_end_to_end_via_real_oms_open_and_close(session):
    """The real OrderManager persists ``opened_confidence`` on open, and
    ``_reentry_blocked`` reads that same row back after close — proving the
    two pieces are actually wired together, not just unit-correct in
    isolation."""
    _paper(session, cash=50_000)
    session.flush()
    oms = OrderManager(session, PaperBroker(50_000))

    decision = _decision_stub("INFY", "equity_cash", 1500.0, confidence=0.60)
    out = oms._send(decision, venue="paper", broker=oms.paper)
    assert out["ok"] is True
    pos = session.query(Position).filter_by(symbol="INFY", status="open").one()
    assert pos.opened_confidence == pytest.approx(0.60)

    closed = oms.close_position(pos, price=1510.0, reason="test close")
    assert closed["ok"] is True
    session.flush()
    now = _now()

    settings = get_settings()
    # Immediately after close, a similar-confidence signal is held.
    assert _reentry_blocked(session, "INFY", 0.62, now, settings) is not None
    # A materially stronger signal is allowed straight through.
    assert _reentry_blocked(session, "INFY", 0.90, now, settings) is None


def _decision_stub(symbol, market, price, *, confidence, qty=1):
    from types import SimpleNamespace

    from meridian_v3.capital.sizer import SizePlan

    plan = SizePlan(
        qty=qty, notional=0.0, risk_rupees=0.0, risk_pct=0.0, kelly_frac=0.0,
        stop=0.0, market=market, horizon="intraday", reason="", blocked=False,
    )
    return SimpleNamespace(
        symbol=symbol, action="buy", market=market, price=price,
        size=plan, paper=True, live=False, confidence=confidence,
    )
