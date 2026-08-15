"""Phase 0.2 / 0.3 — the OMS must never fabricate or accept a bad fill price."""

from datetime import datetime, timezone
from types import SimpleNamespace

from meridian_v3.capital.sizer import SizePlan
from meridian_v3.execution.brokers.paper_broker import PaperBroker
from meridian_v3.execution.oms import OrderManager
from meridian_v3.storage.schema import AccountState, Fill, Order, Position, PriceCache


def _now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _paper(session, cash=500_000.0):
    session.add(AccountState(venue="paper", cash=cash, equity=cash, peak=cash, updated_at=_now()))


def _plan(market, qty):
    return SizePlan(
        qty=qty, notional=0.0, risk_rupees=0.0, risk_pct=0.0, kelly_frac=0.0,
        stop=0.0, market=market, horizon="intraday", reason="", blocked=False,
    )


def _decision(symbol, market, price, qty=0.01, action="buy"):
    return SimpleNamespace(
        symbol=symbol, action=action, market=market, price=price,
        size=_plan(market, qty), paper=True, live=False,
    )


def test_refuses_to_fabricate_a_fill_when_price_is_missing(session):
    """0.2 — an empty mark is a Hold, never notional/qty and never a fill."""
    _paper(session)
    session.flush()
    oms = OrderManager(session, PaperBroker(500_000))
    for market in ("india_futures", "global_commodities"):
        out = oms._send(_decision(f"X_{market}", market, 0.0), venue="paper", broker=oms.paper)
        assert out["ok"] is False
        assert out["status"] == "rejected"
    session.flush()
    assert session.query(Fill).count() == 0
    assert session.query(Position).count() == 0
    assert session.query(Order).filter_by(status="rejected").count() == 2


def test_rejects_a_fill_far_from_the_tape(session):
    """0.3 — a 1/10th margin-slide mark cannot reach a Fill even if 0.2 slips."""
    _paper(session)
    session.add(PriceCache(symbol="GOLD.X", last=423_642.0, quality="live"))
    session.flush()
    oms = OrderManager(session, PaperBroker(500_000))
    out = oms._send(_decision("GOLD.X", "global_commodities", 42_364.0), venue="paper", broker=oms.paper)
    assert out["ok"] is False
    assert out["status"] == "rejected"
    session.flush()
    assert session.query(Fill).count() == 0
    assert session.query(Position).count() == 0


def test_fills_when_price_matches_the_tape(session):
    """Positive control: an honest mark at the tape still fills normally."""
    _paper(session)
    session.add(PriceCache(symbol="GOLD.X", last=42_364.0, quality="live"))
    session.flush()
    oms = OrderManager(session, PaperBroker(500_000))
    out = oms._send(_decision("GOLD.X", "global_commodities", 42_364.0), venue="paper", broker=oms.paper)
    assert out["ok"] is True
    session.flush()
    assert session.query(Fill).count() == 1
    assert session.query(Position).filter_by(status="open").count() == 1


def test_options_sell_without_a_held_position_is_rejected(session):
    """2.1 — second line of defense: a sell on a buying-only market with
    nothing open to close must be refused at the OMS boundary, independent
    of decision/engine.py's own short_ok gate."""
    _paper(session)
    session.add(PriceCache(symbol="NIFTY.C", last=120.0, quality="live"))
    session.flush()
    for market in ("options_buy", "crypto_options"):
        oms = OrderManager(session, PaperBroker(500_000))
        out = oms._send(
            _decision(f"X_{market}", market, 10.0, action="sell"), venue="paper", broker=oms.paper
        )
        assert out["ok"] is False
        assert out["status"] == "rejected"
    session.flush()
    assert session.query(Fill).count() == 0
    assert session.query(Position).count() == 0
    assert session.query(Order).filter_by(status="rejected").count() == 2


def test_options_sell_to_close_a_real_holding_still_fills(session):
    """Positive control: a sell that actually closes an open options clip
    is not caught by the new 2.1 guard."""
    _paper(session)
    oms = OrderManager(session, PaperBroker(500_000))
    buy = oms._send(_decision("NIFTY.C", "options_buy", 120.0, qty=1, action="buy"), venue="paper", broker=oms.paper)
    assert buy["ok"] is True
    out = oms._send(_decision("NIFTY.C", "options_buy", 130.0, qty=1, action="sell"), venue="paper", broker=oms.paper)
    assert out["ok"] is True
    session.flush()
    assert session.query(Position).filter_by(status="closed").count() == 1


def test_forex_micro_standard_lot_is_rejected(session):
    """2.1 — forex_micro forbids standard lots; a qty at/above the ceiling
    must be refused even though the sizer itself never produces one."""
    _paper(session)
    session.add(PriceCache(symbol="EURUSD", last=90.0, quality="live"))
    session.flush()
    oms = OrderManager(session, PaperBroker(500_000))
    out = oms._send(_decision("EURUSD", "forex_micro", 90.0, qty=1.0), venue="paper", broker=oms.paper)
    assert out["ok"] is False
    assert out["status"] == "rejected"
    session.flush()
    assert session.query(Fill).count() == 0
    assert session.query(Position).count() == 0


def test_forex_micro_nano_lot_still_fills(session):
    """Positive control: a genuine nano/micro lot under the ceiling still fills."""
    _paper(session)
    session.add(PriceCache(symbol="EURUSD", last=90.0, quality="live"))
    session.flush()
    oms = OrderManager(session, PaperBroker(500_000))
    out = oms._send(_decision("EURUSD", "forex_micro", 90.0, qty=0.01), venue="paper", broker=oms.paper)
    assert out["ok"] is True
