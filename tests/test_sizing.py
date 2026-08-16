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


def test_option_stop_is_a_fraction_of_premium_not_the_whole_of_it():
    """An option's stop must be room to move, like every other market's.

    The sizer used to set `stop = price`, i.e. the entire premium, so an
    option never stopped out until it was worthless -- while the decision
    gate modelled a 2 x ATR loss. The two disagreed about the same trade.
    An ATR stop is also the wrong instrument here: a weekly ATM option's
    daily ATR is ~33% of premium, so 2 x ATR is a 67% stop.
    """
    from meridian_v3.capital.sizer import size_position
    from meridian_v3.config import Settings
    from meridian_v3.engine.drawdown import assess_drawdown

    s = Settings()
    equity, premium = 100_000.0, 2_000.0
    plan = size_position(
        equity=equity, cash=equity, price=premium, atr=premium * 0.33,
        p_success=0.65, payoff=1.5, confidence=0.7,
        drawdown=assess_drawdown(equity, equity),
        settings=s, market="options_buy", open_count=0,
    )
    assert not plan.blocked
    expected = premium * s.markets.options_buy.stop_pct_of_premium
    assert abs(plan.stop - expected) < 1e-6, "stop should be a fraction of premium"
    assert plan.stop < premium, "stop must leave something to salvage"


def test_option_premium_is_capped_by_the_risk_budget_not_just_the_premium_cap():
    """A 12%-of-equity premium at a 50% stop risks 6% of the book on one
    clip, against a 1.5% cap everywhere else. The premium ceiling must be
    whichever is tighter."""
    from meridian_v3.capital.sizer import size_position
    from meridian_v3.config import Settings
    from meridian_v3.engine.drawdown import assess_drawdown

    s = Settings()
    equity = 100_000.0
    stop_pct = s.markets.options_buy.stop_pct_of_premium

    def _plan(premium: float):
        return size_position(
            equity=equity, cash=equity, price=premium, atr=premium * 0.33,
            p_success=0.65, payoff=1.5, confidence=0.7,
            drawdown=assess_drawdown(equity, equity),
            settings=s, market="options_buy", open_count=0,
        )

    # Sits inside the explicit 12% premium cap, but a stop-out would cost
    # ~6% of the book -- must be refused.
    fat = equity * s.markets.options_buy.max_premium_pct_of_equity
    assert _plan(fat).blocked

    # Every accepted clip stays within the normal risk budget.
    for premium in (500.0, 1_500.0, 3_000.0):
        plan = _plan(premium)
        if not plan.blocked:
            risk = premium * stop_pct
            assert risk <= equity * s.sizing.max_risk_pct_normal + 1e-6


def test_option_target_clears_its_own_cost_hurdle():
    """The point of settling the shape: a 150% target has to beat the ~11.5%
    an option round trip demands, or options can never be worth taking."""
    from meridian_v3.config import Settings
    from meridian_v3.engine.edge import round_trip_cost_pct

    s = Settings()
    target_pct = s.markets.options_buy.stop_pct_of_premium * s.sizing.target_r_multiple
    hurdle = round_trip_cost_pct("options_buy") * s.decision.min_reward_cost_multiple
    assert target_pct > hurdle * 5, f"target {target_pct:.0%} vs hurdle {hurdle:.1%}"
