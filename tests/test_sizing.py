from meridian_v3.capital.sizer import size_position, stop_price
from meridian_v3.config import Settings
from meridian_v3.engine.drawdown import assess_drawdown


def test_reserve_does_not_trap_leftover_cash():
    plan = size_position(
        equity=50_000, cash=6_000, price=200, atr=8, p_success=0.62, payoff=1.4,
        confidence=0.8, drawdown=assess_drawdown(50_000, 50_000), settings=Settings(),
    )
    assert not plan.blocked
    assert plan.qty >= 1


def test_fifty_thousand_book_can_buy_several_shares():
    plan = size_position(
        equity=50_000, cash=50_000, price=1400, atr=20, p_success=0.62, payoff=1.4,
        confidence=0.8, drawdown=assess_drawdown(50_000, 50_000), settings=Settings(),
    )
    assert not plan.blocked
    assert plan.qty >= 1
    assert plan.notional <= 50_000


def test_options_selling_never_sized_as_home():
    plan = size_position(
        equity=50_000, cash=50_000, price=900, atr=10, p_success=0.7, payoff=1.2,
        confidence=0.9, drawdown=assess_drawdown(50_000, 50_000), settings=Settings(),
        market="options_buy",
    )
    assert plan.market == "options_buy"
    if not plan.blocked:
        assert plan.qty == 1
        assert "buying only" in plan.reason.lower()


def test_stop_distance_ratio_ceiling_is_a_config_knob_not_045():
    """2.9 — a large-ATR distance is treated as a price line at whatever ratio
    ``sizing.stop_distance_ratio_ceiling`` says, not a hardcoded 0.45.

    A ₹100 instrument with ₹40 of ATR room sits at ratio 0.40 — under the old
    hardcoded 0.45 that's still "distance" and would convert to a price line.
    Tighten the ceiling to 0.30 in settings and the same ₹40 must now be read
    as an already-a-price-line stop instead (function returns it unchanged).
    """
    default_settings = Settings()
    # Default ceiling (0.45): ₹40 distance on ₹100 entry (ratio 0.40) is still
    # "room" — a buy stops below entry.
    line = stop_price("buy", 100.0, 40.0, default_settings)
    assert line == 60.0

    tight = Settings()
    tight.sizing.stop_distance_ratio_ceiling = 0.30
    # Same inputs, tighter ceiling: 0.40 now exceeds 0.30, so the "distance"
    # is treated as an already-a-price-line stop and returned unchanged.
    line = stop_price("buy", 100.0, 40.0, tight)
    assert line == 40.0
