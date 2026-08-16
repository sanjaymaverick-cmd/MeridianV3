"""The crypto sleeve is pulled live from Binance and cut by turnover.

Two things this locks in:

1. `is_binance_symbol` must not be a hardcoded ten-symbol set. It was, which
   meant any symbol added to the universe beyond those ten was not routed to
   Binance at all — it fell through to the yfinance batch, which has no quote
   for e.g. "PLUMEUSDT", and came back a missing mark. Widening the universe
   without fixing this would have silently produced dead symbols.
2. The turnover cut, and the offline fallback.

Every test here monkeypatches the network — CI must not depend on Binance
being reachable, and a seed must not cost ~2 HTTP calls per test.
"""

from __future__ import annotations

from meridian_v3.data_providers import binance as binance_mod
from meridian_v3.universe.crypto import BINANCE_UNIVERSE, expand_binance_universe


def test_is_binance_symbol_is_not_limited_to_the_seed_ten(monkeypatch):
    """A newly listed pair must route to Binance, not fall through to yfinance."""
    monkeypatch.setattr(binance_mod, "_spot_roots_cache", {"BTCUSDT", "PLUMEUSDT", "HEMIUSDT"})
    assert binance_mod.is_binance_symbol("PLUMEUSDT") is True
    assert binance_mod.is_binance_symbol("HEMIUSDT") is True
    # Derivative suffixes resolve to their spot root.
    assert binance_mod.is_binance_symbol("BTCUSDT.F") is True
    assert binance_mod.is_binance_symbol("RELIANCE") is False


def test_spot_roots_falls_back_to_seed_when_binance_is_unreachable(monkeypatch):
    """Offline must not unroute every crypto symbol at once — that would turn
    the whole sleeve into missing marks."""
    monkeypatch.setattr(binance_mod, "_spot_roots_cache", None)

    def _boom(*a, **k):
        raise OSError("network down")

    import httpx

    monkeypatch.setattr(httpx, "get", _boom)
    roots = binance_mod.spot_roots(refresh=True)
    assert roots == binance_mod.SEED_SPOT_ROOTS
    assert "BTCUSDT" in roots


def test_expand_universe_keeps_static_sleeve_and_adds_liquid_pairs(monkeypatch):
    monkeypatch.setattr(
        binance_mod,
        "liquid_usdt_pairs",
        lambda min_quote_volume=0.0, limit=None: [("AAAUSDT", 9e6), ("BBBUSDT", 7e6)],
    )
    # expand_binance_universe imports the symbol at call time from the module.
    import meridian_v3.universe.crypto as crypto_mod

    monkeypatch.setattr(
        crypto_mod,
        "expand_binance_universe",
        crypto_mod.expand_binance_universe,
    )
    rows = expand_binance_universe(min_quote_volume=5e6)
    symbols = {r[0] for r in rows}
    # The static sleeve (incl. futures/options rows) survives ...
    assert {r[0] for r in BINANCE_UNIVERSE} <= symbols
    # ... and the liquid additions are present, tagged as spot crypto.
    assert "AAAUSDT" in symbols and "BBBUSDT" in symbols
    added = next(r for r in rows if r[0] == "AAAUSDT")
    assert added[1] == "crypto"


def test_expand_universe_falls_back_to_static_when_binance_is_unreachable(monkeypatch):
    monkeypatch.setattr(binance_mod, "liquid_usdt_pairs", lambda **k: [])
    assert expand_binance_universe() == BINANCE_UNIVERSE


def test_liquid_pairs_applies_the_turnover_cut_and_orders_richest_first(monkeypatch):
    import httpx

    monkeypatch.setattr(binance_mod, "_spot_roots_cache", {"AAAUSDT", "BBBUSDT", "DUSTUSDT"})

    class _Res:
        def raise_for_status(self):
            pass

        def json(self):
            return [
                {"symbol": "AAAUSDT", "quoteVolume": "3000000"},
                {"symbol": "BBBUSDT", "quoteVolume": "9000000"},
                {"symbol": "DUSTUSDT", "quoteVolume": "1000"},
                {"symbol": "NOTLISTED", "quoteVolume": "9999999999"},
            ]

    monkeypatch.setattr(httpx, "get", lambda *a, **k: _Res())
    pairs = binance_mod.liquid_usdt_pairs(min_quote_volume=2_000_000)
    assert [p[0] for p in pairs] == ["BBBUSDT", "AAAUSDT"]  # richest first, dust cut
    # A symbol Binance isn't listing as TRADING/spot must never appear.
    assert all(p[0] != "NOTLISTED" for p in pairs)


def test_install_universe_skips_the_network_under_a_test_db(session):
    """A seed must not make network calls per test — that turned the suite
    from 30s into 114s and made it depend on Binance being up."""
    import meridian_v3.universe.crypto as crypto_mod
    from meridian_v3.universe.nse_bse import install_universe

    called = []
    monkey_target = crypto_mod.expand_binance_universe
    crypto_mod.expand_binance_universe = lambda **k: called.append(1) or BINANCE_UNIVERSE
    try:
        install_universe(session)
        session.flush()
    finally:
        crypto_mod.expand_binance_universe = monkey_target
    assert called == []  # MERIDIAN_V3_TEST_DB is set by conftest
