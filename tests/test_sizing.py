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


def test_cash_reserve_is_a_floor_not_a_sliding_fraction():
    """The reserve must protect the same rupee amount however little cash is
    left. It used to be min(equity * pct, cash * 0.35), so it shrank with the
    cash it was meant to protect -- at Rs300 of cash it held back Rs105 -- and
    the live book ran itself down to Rs106 (0.11% of equity) in one cycle
    while the setting said 10%.
    """
    from meridian_v3.capital.sizer import size_position
    from meridian_v3.config import Settings
    from meridian_v3.engine.drawdown import assess_drawdown

    settings = Settings()
    equity = 100_000.0
    floor = equity * settings.sizing.cash_reserve_pct

    def _plan(cash: float):
        return size_position(
            equity=equity, cash=cash, price=1000.0, atr=20.0,
            p_success=0.62, payoff=1.4, confidence=0.7,
            drawdown=assess_drawdown(equity, equity),
            settings=settings, market="equity_cash", open_count=0,
        )

    # Cash at exactly the reserve leaves nothing spendable.
    assert _plan(floor).qty == 0
    # Below the reserve, still nothing -- it must not scale down and continue.
    assert _plan(floor * 0.5).qty == 0
    assert _plan(100.0).qty == 0
    # Comfortably above it, sizing works normally.
    assert _plan(equity).qty > 0
