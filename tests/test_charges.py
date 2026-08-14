import json
from datetime import datetime, timezone

from meridian_v3.charges.indian import broker_label, levy, normalize_broker
from meridian_v3.execution.brokers.paper_broker import PaperBroker
from meridian_v3.execution.oms import OrderManager
from meridian_v3.storage.schema import AccountState, Fill, Position
from meridian_v3.ui.book_view import decorate_fills, decorate_positions, ensure_fill_charges


def _now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def test_broker_aliases():
    assert normalize_broker("Angel One") == "angel_one"
    assert normalize_broker("ICICI-Direct") == "icici_direct"
    assert normalize_broker("kite") == "zerodha"
    assert broker_label("angel") == "Angel One"


def test_zerodha_delivery_has_zero_brokerage_and_stt():
    bill = levy(broker="zerodha", market="equity_cash", side="buy", qty=2, price=1400, product="CNC")
    assert bill.brokerage == 0.0
    assert bill.stt == 2.8  # 0.1% of ₹2,800
    assert bill.stamp == 0.42  # 0.015% of buy
    assert bill.gst < 0.1  # GST only on exchange + SEBI
    assert bill.total == round(bill.brokerage + bill.stt + bill.exchange + bill.sebi + bill.stamp + bill.gst, 2)


def test_angel_matches_zerodha_discount_plan():
    z = levy(broker="zerodha", market="equity_cash", side="sell", qty=10, price=500, product="MIS")
    a = levy(broker="angel_one", market="equity_cash", side="sell", qty=10, price=500, product="MIS")
    assert a.brokerage == z.brokerage == 1.5
    assert a.stt == z.stt == 1.25  # 0.025% of sell
    assert a.gst == z.gst


def test_icici_delivery_charges_percent_brokerage():
    bill = levy(broker="icici_direct", market="equity_cash", side="buy", qty=2, price=1400, product="CNC")
    assert bill.brokerage == 15.4  # 0.55% of ₹2,800
    assert bill.gst == round(0.18 * (15.4 + bill.exchange + bill.sebi), 2)
    assert bill.total > 15.4


def test_crypto_has_brokerage_gst_and_tds():
    bill = levy(broker="zerodha", market="crypto_spot", side="buy", qty=0.001, price=8_000_000)
    assert bill.turnover == 8000
    assert bill.brokerage == 8.0
    assert bill.gst == 1.44
    assert bill.tds == 80.0
    assert bill.sebi == 0.0
    assert bill.total == 89.44


def test_paper_cash_falls_by_fees(session):
    session.add(
        AccountState(
            venue="paper", cash=50_000, equity=50_000, peak=50_000,
            broker="zerodha", updated_at=_now(),
        )
    )
    session.flush()
    paper = PaperBroker(50_000)
    oms = OrderManager(session, paper)
    out = oms.execute(_decision())
    assert out["paper"]["ok"]
    fill = session.query(Fill).one()
    assert fill.fees > 0
    raw = json.loads(fill.charges_json)
    assert raw["broker"] == "zerodha"
    assert raw["brokerage"] == 0.0
    assert raw["gst"] >= 0
    assert paper.funds() == 50_000 - 2800 - fill.fees
    assert "GST" in fill.note


def test_icici_sheet_used_when_account_picks_it(session):
    session.add(
        AccountState(
            venue="paper", cash=50_000, equity=50_000, peak=50_000,
            broker="icici_direct", updated_at=_now(),
        )
    )
    session.flush()
    paper = PaperBroker(50_000)
    oms = OrderManager(session, paper)
    oms.execute(_decision())
    fill = session.query(Fill).one()
    raw = json.loads(fill.charges_json)
    assert raw["broker"] == "icici_direct"
    assert raw["brokerage"] == 15.4


def test_open_pnl_nets_fees(session):
    from meridian_v3.storage.schema import PriceCache

    session.add(PriceCache(symbol="INFY", last=1600, quality="live"))
    session.add(
        Position(
            venue="paper", market="equity_cash", symbol="INFY", side="buy",
            qty=2, avg_price=1400, status="open", source="test", opened_at=_now(),
        )
    )
    session.add(
        Fill(
            venue="paper", symbol="INFY", side="buy", qty=2, price=1400,
            fees=3.31, charges_json='{"brokerage":0,"gst":0.02,"stt":2.8,"total":3.31}',
            filled_at=_now(), note="test",
        )
    )
    session.flush()
    cards = decorate_positions(session, list(session.query(Position)))
    assert cards[0]["fees"] == 3.31
    assert cards[0]["pnl"] == 400 - 3.31


def test_backfill_writes_missing_bills(session):
    session.add(
        Fill(
            venue="paper", symbol="TCS", side="buy", qty=1, price=3000,
            fees=0, charges_json="{}", filled_at=_now(), note="old",
        )
    )
    session.flush()
    written = ensure_fill_charges(session, "zerodha")
    assert written == 1
    fill = session.query(Fill).one()
    assert fill.fees > 0
    rows = decorate_fills([fill])
    assert rows[0]["stt"] == 3.0
    assert rows[0]["brokerage"] == 0.0


def _decision(symbol="INFY", qty=2, price=1400, horizon="swing"):
    from meridian_v3.capital.sizer import SizePlan
    from meridian_v3.decision.engine import AutoDecision
    from meridian_v3.domain.reviews import PlainReview, ReviewScenario
    from meridian_v3.safety.guards import SafetyVerdict

    size = SizePlan(
        qty=qty,
        notional=qty * price,
        risk_rupees=40,
        risk_pct=0.01,
        kelly_frac=0.1,
        stop=price * 0.97,
        market="equity_cash",
        horizon=horizon,
        reason="test",
        blocked=False,
    )
    review = PlainReview(
        title="Test look (not an order)",
        scenario=ReviewScenario.AUTO_DECISION,
        status_lines=["ok"],
        daily_pnl_line="quiet day",
        gamma_scalp_line="no jump",
        suggestion="watch",
    )
    return AutoDecision(
        symbol=symbol,
        action="buy",
        side="buy",
        confidence=0.7,
        p_success=0.6,
        confluence=70,
        freshness=1.0,
        market="equity_cash",
        paper=True,
        live=False,
        size=size,
        price=price,
        safety=SafetyVerdict(True, False, ("paper only",)),
        review=review,
        reasons=["test"],
    )
