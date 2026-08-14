from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from meridian_v3.capital.sizer import size_position
from meridian_v3.config import Settings
from meridian_v3.engine.drawdown import assess_drawdown
from meridian_v3.router.calendar import coverage_note, market_session
from meridian_v3.router.markets import market_for, route_market
from meridian_v3.safety.guards import evaluate_safety
from meridian_v3.universe import COMMODITY_UNIVERSE, FX_UNIVERSE


IST = ZoneInfo("Asia/Kolkata")
ET = ZoneInfo("America/New_York")


def _safety(now, market, **kwargs):
    args = dict(
        drawdown=assess_drawdown(50_000, 50_000),
        settings=Settings(),
        live_armed=True,
        live_today=0,
        confidence=0.9,
        market=market,
        horizon="intraday",
        now=now,
    )
    args.update(kwargs)
    return evaluate_safety(**args)


def test_universes_cover_commodities_and_fx():
    cmdty = {row[0] for row in COMMODITY_UNIVERSE}
    fx = {row[0] for row in FX_UNIVERSE}
    assert {"GOLD.X", "CRUDE.X", "COPPER.X"} <= cmdty
    assert {"USDINR", "EURUSD", "GBPUSD", "USDJPY"} <= fx


def test_router_prefers_named_tape():
    assert market_for("commodity", "SILVER.X") == "global_commodities"
    assert market_for("fx", "GBPINR") == "forex_micro"
    # 1.3 — routing is suffix-based (market_for); route_market just carries
    # whatever market_for resolved through as `preferred`.
    route = route_market(preferred=market_for("commodity", "SILVER.X"))
    assert route.market == "global_commodities"


def test_india_closed_friday_after_1530():
    now = datetime(2026, 8, 14, 16, 0, tzinfo=IST)  # Friday
    india = market_session(now, Settings(), "equity_cash")
    crypto = market_session(now, Settings(), "crypto_spot")
    fx = market_session(now, Settings(), "forex_micro")
    cmdty = market_session(now, Settings(), "global_commodities")
    assert india.in_session is False
    assert crypto.in_session is True
    assert crypto.always_on is True
    assert fx.in_session is True  # still Friday morning ET
    assert cmdty.in_session is True


def test_saturday_is_crypto_only():
    now = datetime(2026, 8, 15, 12, 0, tzinfo=IST)  # Saturday
    assert market_session(now, Settings(), "equity_cash").in_session is False
    assert market_session(now, Settings(), "forex_micro").in_session is False
    assert market_session(now, Settings(), "global_commodities").in_session is False
    assert market_session(now, Settings(), "crypto_spot").in_session is True
    assert market_session(now, Settings(), "crypto_futures").in_session is True


def test_sunday_evening_et_reopens_fx_and_commodities():
    now = datetime(2026, 8, 16, 19, 0, tzinfo=ET)  # Sunday 19:00 ET
    assert market_session(now, Settings(), "equity_cash").in_session is False
    assert market_session(now, Settings(), "forex_micro").in_session is True
    assert market_session(now, Settings(), "global_commodities").in_session is True
    assert market_session(now, Settings(), "crypto_spot").in_session is True


def test_live_crypto_not_blocked_when_india_is_shut():
    now = datetime(2026, 8, 14, 16, 0, tzinfo=IST)
    crypto = _safety(now, "crypto_spot")
    equity = _safety(now, "equity_cash")
    fx = _safety(now, "forex_micro")
    assert crypto.allow_paper and crypto.allow_live
    assert equity.allow_paper and equity.allow_live is False
    assert fx.allow_paper and fx.allow_live


def test_live_fx_blocked_on_saturday():
    now = datetime(2026, 8, 15, 12, 0, tzinfo=IST)
    fx = _safety(now, "forex_micro")
    crypto = _safety(now, "crypto_spot")
    assert fx.allow_live is False
    assert crypto.allow_live is True


def test_coverage_note_names_the_weekend_gap():
    friday = datetime(2026, 8, 14, 16, 5, tzinfo=IST)
    note = coverage_note(friday, Settings())
    assert "shut" in note.lower()
    assert "crypto" in note.lower()


def test_commodity_sizer_fits_fifty_thousand_book():
    plan = size_position(
        equity=50_000,
        cash=50_000,
        price=220_000,
        atr=2_000,
        p_success=0.62,
        payoff=1.4,
        confidence=0.8,
        drawdown=assess_drawdown(50_000, 50_000),
        settings=Settings(),
        market="global_commodities",
    )
    assert not plan.blocked
    assert plan.qty >= 0.01
    assert plan.market == "global_commodities"
    assert abs(plan.notional - plan.qty * 220_000) < 1.0
    assert "commodity" in plan.reason.lower()


def test_oms_records_rupee_mark_not_margin(session):
    from meridian_v3.execution.brokers.paper_broker import PaperBroker
    from meridian_v3.execution.oms import OrderManager
    from meridian_v3.storage.schema import AccountState, Fill
    from tests.test_charges import _decision

    now = datetime.now(timezone.utc).replace(tzinfo=None)
    session.add(
        AccountState(
            venue="paper",
            name="test",
            cash=50_000,
            equity=50_000,
            peak=50_000,
            live_armed=0,
            paper_auto=0,
            updated_at=now,
        )
    )
    session.flush()
    gold = 423_642.60
    decision = _decision(symbol="GOLD.X", qty=0.02, price=gold)
    decision.market = "global_commodities"
    decision.size = decision.size.__class__(
        qty=0.02,
        notional=gold * 0.02 * 0.10,
        risk_rupees=80,
        risk_pct=0.01,
        kelly_frac=0.1,
        stop=gold * 0.015,
        market="global_commodities",
        horizon="intraday",
        reason="test",
        blocked=False,
    )
    decision.price = gold
    oms = OrderManager(session, PaperBroker(50_000))
    out = oms.execute(decision)
    assert out["paper"]["ok"]
    fill = session.query(Fill).filter_by(symbol="GOLD.X").one()
    assert abs(fill.price - gold) < 0.1
    assert fill.price > 100_000


def test_repair_lifts_ten_x_commodity_avg(session):
    from meridian_v3.storage.repair import repair_margin_priced_clips
    from meridian_v3.storage.schema import AccountState, Position, PriceCache
    from meridian_v3.storage.seed import seed_demo

    seed_demo(session, reset=True)
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    last = 423_642.60
    session.add(PriceCache(symbol="GOLD.X", last=last, quality="live"))
    session.add(
        Position(
            venue="paper",
            market="global_commodities",
            symbol="GOLD.X",
            side="buy",
            qty=0.02,
            avg_price=42_364.26,
            stop=0,
            horizon="intraday",
            status="open",
            source="test",
            opened_at=now,
        )
    )
    session.flush()
    n = repair_margin_priced_clips(session)
    assert n == 1
    pos = session.query(Position).filter_by(symbol="GOLD.X").one()
    assert abs(pos.avg_price - 423_642.60) < 1.0
    paper = session.query(AccountState).filter_by(venue="paper").one()
    assert paper.cash < 50_000
