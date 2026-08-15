from datetime import datetime
from zoneinfo import ZoneInfo

from meridian_v3.config import Settings
from meridian_v3.engine.drawdown import assess_drawdown
from meridian_v3.router.markets import route_market
from meridian_v3.safety.guards import evaluate_safety


def test_route_follows_the_preferred_market():
    # 1.3 — routing is suffix/asset-class dispatch: `route_market` just
    # returns whatever `market_for` decided, there is no score competition.
    route = route_market(preferred="options_buy")
    assert route.market == "options_buy"


def test_route_defaults_to_equity_cash_with_no_preference():
    route = route_market()
    assert route.market == "equity_cash"


def test_live_requires_arm():
    now = datetime(2026, 8, 14, 10, 30, tzinfo=ZoneInfo("Asia/Kolkata"))
    v = evaluate_safety(
        drawdown=assess_drawdown(5000, 5000),
        settings=Settings(),
        live_armed=False,
        live_today=0,
        confidence=0.9,
        market="equity_cash",
        horizon="intraday",
        now=now,
    )
    assert v.allow_paper
    assert v.allow_live is False
