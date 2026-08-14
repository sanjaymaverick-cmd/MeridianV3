from meridian_v3.capital.sizer import size_position
from meridian_v3.config import Settings
from meridian_v3.data_providers.binance import binance_pair, is_binance_symbol
from meridian_v3.engine.drawdown import assess_drawdown
from meridian_v3.router.markets import market_for
from meridian_v3.universe import BINANCE_UNIVERSE, INDIA_DERIV_UNIVERSE


def test_market_for_instruments():
    assert market_for("crypto", "BTCUSDT") == "crypto_spot"
    assert market_for("crypto_futures", "BTCUSDT.F") == "crypto_futures"
    assert market_for("crypto_options", "BTCUSDT.C") == "crypto_options"
    assert market_for("future", "NIFTY.F") == "india_futures"
    assert market_for("option", "NIFTY.C") == "options_buy"
    assert market_for("equity", "RELIANCE") == "equity_cash"


def test_binance_pair_helpers():
    assert is_binance_symbol("BTCUSDT.F")
    assert binance_pair("ETHUSDT.C") == "ETHUSDT"
    assert not is_binance_symbol("RELIANCE")


def test_universes_include_crypto_and_india_fo():
    coins = {row[0] for row in BINANCE_UNIVERSE}
    derivs = {row[0] for row in INDIA_DERIV_UNIVERSE}
    assert "BTCUSDT" in coins
    assert "BTCUSDT.F" in coins
    assert "ETHUSDT.C" in coins
    assert "NIFTY.F" in derivs
    assert "NIFTY.C" in derivs


def _book(**kwargs):
    base = dict(
        equity=50_000,
        cash=50_000,
        price=5_000_000,
        atr=80_000,
        p_success=0.62,
        payoff=1.4,
        confidence=0.8,
        drawdown=assess_drawdown(50_000, 50_000),
        settings=Settings(),
    )
    base.update(kwargs)
    return size_position(**base)


def test_crypto_spot_fractional():
    plan = _book(market="crypto_spot")
    assert not plan.blocked
    assert 0 < plan.qty < 1
    assert plan.market == "crypto_spot"


def test_crypto_futures_uses_margin():
    plan = _book(market="crypto_futures")
    assert not plan.blocked
    assert plan.notional < plan.qty * 5_000_000
    assert "future" in plan.reason.lower()


def test_india_futures_mini_lot():
    plan = _book(price=24500, atr=200, market="india_futures")
    assert not plan.blocked
    assert plan.qty >= 0.05
    assert "mini" in plan.reason.lower()


def test_options_still_buy_only():
    plan = _book(price=180, atr=10, market="options_buy")
    assert plan.market == "options_buy"
    if not plan.blocked:
        assert "buying only" in plan.reason.lower()
