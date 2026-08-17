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


def test_forex_lot_is_contract_units_not_raw_currency_units():
    """An FX lot of 1.0 is `contract_size` units of the base currency.

    The sizer used to read `min_lot=0.01` as 0.01 *units*, handing back a
    USDINR clip worth Rs0.84 regardless of equity, confidence or edge.
    Nothing that small can clear a safety pad, so forex took zero trades in
    a five-year backtest -- 122 signals cleared the meta-label and every one
    died at the edge filter.
    """
    from meridian_v3.capital.sizer import size_position
    from meridian_v3.config import Settings
    from meridian_v3.engine.drawdown import assess_drawdown

    s = Settings()
    equity, price = 100_000.0, 84.0  # USDINR
    plan = size_position(
        equity=equity, cash=equity, price=price, atr=0.25,
        p_success=0.62, payoff=1.4, confidence=0.70,
        drawdown=assess_drawdown(equity, equity),
        settings=s, market="forex_micro", open_count=0,
    )
    assert not plan.blocked
    units = plan.qty * s.markets.forex_micro.contract_size
    assert units >= 100, f"a nano lot is 100 units, got {units}"
    # Notional must be a real position, not pocket change.
    assert plan.notional > 1_000, f"notional {plan.notional} is not a tradeable clip"
    assert abs(plan.notional - units * price) < 1.0


def test_forex_respects_the_standard_lot_ban_and_position_cap():
    """Nano/micro only: a standard lot (>= standard_lot_qty) must never be
    produced, and the position cap still binds."""
    from meridian_v3.capital.sizer import size_position
    from meridian_v3.config import Settings
    from meridian_v3.engine.drawdown import assess_drawdown

    s = Settings()
    equity = 100_000.0
    for price, atr in ((84.0, 0.25), (0.55, 0.003), (122.0, 0.75)):
        plan = size_position(
            equity=equity, cash=equity, price=price, atr=atr,
            p_success=0.62, payoff=1.4, confidence=0.70,
            drawdown=assess_drawdown(equity, equity),
            settings=s, market="forex_micro", open_count=0,
        )
        if plan.blocked:
            continue
        assert plan.qty < s.markets.forex_micro.standard_lot_qty, "standard lots are forbidden"
        assert plan.notional <= equity * s.sizing.max_position_pct + 1.0
        assert plan.risk_rupees <= equity * s.sizing.max_risk_pct_normal + 1.0


def test_edge_safety_pad_scales_with_the_clip_not_the_book():
    """The pad was `edge_safety_margin * equity * 0.25` -- a flat Rs37.50 on
    a Rs100,000 book, applied identically to a Rs18,000 clip and a Rs100 one.
    An absolute hurdle makes small clips arithmetically unable to pass however
    good the signal, which is what shut forex out entirely."""
    from datetime import datetime
    from zoneinfo import ZoneInfo

    from meridian_v3.config import Settings
    from meridian_v3.decision.engine import DecisionInput, decide
    from meridian_v3.engine.confluence import FactorVote
    from meridian_v3.engine.drawdown import assess_drawdown
    from meridian_v3.engine.edge import estimate_equity_costs
    from meridian_v3.engine.meta_label import PrimarySignal

    now = datetime(2026, 8, 17, 11, 0, tzinfo=ZoneInfo("Asia/Kolkata"))

    def _take(price: float) -> bool:
        atr = price * 0.02
        return decide(
            DecisionInput(
                symbol="X", price=price, atr=atr, created_at=now,
                primary=PrimarySignal(1, 1.4, "trend is up"),
                votes=[
                    FactorVote("trend", 0.8, 1.0, "up"),
                    FactorVote("breakout", 0.6, 0.8, "high"),
                    FactorVote("score", 0.5, 0.7, "quality"),
                ],
                win_rupees=atr * 6.0, loss_rupees=atr * 2.0,
                costs=estimate_equity_costs(notional=price), payoff=3.0,
                equity=100_000, cash=100_000,
                drawdown=assess_drawdown(100_000, 100_000),
                live_armed=False, live_today=0, open_count=0,
                preferred_market="equity_cash", now=now,
            ),
            Settings(),
        ).action != "hold"

    # Same signal quality at wildly different unit prices must get the same
    # verdict — the pad can no longer decide it.
    assert _take(50.0) == _take(5_000.0)


def test_contract_markets_report_units_not_just_lots():
    """`qty` is lots in a contract market; `units` is the price-unit count.

    `decide()` multiplies per-unit win/loss/cost figures by the position
    size. Feeding it lots understated an FX clip by 100,000x, which pushed
    every figure under the edge filter's absolute floor -- forex took zero
    trades across a five-year backtest despite 122 signals clearing the
    meta-label.
    """
    from meridian_v3.capital.sizer import size_position
    from meridian_v3.config import Settings
    from meridian_v3.engine.drawdown import assess_drawdown

    s = Settings()
    equity = 100_000.0

    fx = size_position(
        equity=equity, cash=equity, price=84.0, atr=0.25,
        p_success=0.62, payoff=1.4, confidence=0.70,
        drawdown=assess_drawdown(equity, equity),
        settings=s, market="forex_micro", open_count=0,
    )
    assert not fx.blocked
    assert fx.units == fx.qty * s.markets.forex_micro.contract_size
    assert fx.units > fx.qty, "lots and units must not be conflated"
    # Exposure derived from units must match the reported notional.
    assert abs(fx.units * 84.0 - fx.notional) < 1.0

    # A non-contract market leaves `units` at its 0.0 default, which callers
    # read as "same as qty".
    eq = size_position(
        equity=equity, cash=equity, price=1_400.0, atr=28.0,
        p_success=0.62, payoff=1.4, confidence=0.70,
        drawdown=assess_drawdown(equity, equity),
        settings=s, market="equity_cash", open_count=0,
    )
    assert (eq.units or eq.qty) == eq.qty
