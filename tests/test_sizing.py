from meridian_v3.capital.sizer import size_position
from meridian_v3.config import Settings
from meridian_v3.engine.drawdown import assess_drawdown


def test_five_thousand_book_can_buy_one_share():
    plan = size_position(
        equity=5000, cash=5000, price=1400, atr=20, p_success=0.62, payoff=1.4,
        confidence=0.8, drawdown=assess_drawdown(5000, 5000), settings=Settings(),
    )
    assert not plan.blocked
    assert plan.qty >= 1
    assert plan.notional <= 5000


def test_options_selling_never_sized_as_home():
    plan = size_position(
        equity=5000, cash=5000, price=900, atr=10, p_success=0.7, payoff=1.2,
        confidence=0.9, drawdown=assess_drawdown(5000, 5000), settings=Settings(),
        market="options_buy",
    )
    assert plan.market == "options_buy"
    if not plan.blocked:
        assert plan.qty == 1
        assert "buying only" in plan.reason.lower()
