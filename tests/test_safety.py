from datetime import datetime
from zoneinfo import ZoneInfo

from meridian_v3.config import Settings
from meridian_v3.engine.drawdown import assess_drawdown
from meridian_v3.router.markets import route_market
from meridian_v3.safety.guards import evaluate_safety


def test_home_bias():
    route = route_market(equity_score=50, options_score=55, forex_score=40)
    assert route.market == "equity_cash"


def test_shift_when_visitor_is_clearly_stronger():
    route = route_market(equity_score=40, options_score=80, forex_score=10)
    assert route.market == "options_buy"


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
