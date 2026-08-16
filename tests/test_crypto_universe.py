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
            # high/low give each pair a daily range well clear of the
            # peg filter, so this test stays about the turnover cut alone.
            return [
                {"symbol": "AAAUSDT", "quoteVolume": "3000000", "highPrice": "1.05", "lowPrice": "1.00"},
                {"symbol": "BBBUSDT", "quoteVolume": "9000000", "highPrice": "2.10", "lowPrice": "2.00"},
                {"symbol": "DUSTUSDT", "quoteVolume": "1000", "highPrice": "1.05", "lowPrice": "1.00"},
                {"symbol": "NOTLISTED", "quoteVolume": "9999999999", "highPrice": "5.5", "lowPrice": "5.0"},
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


def test_install_universe_deactivates_crypto_that_left_the_universe(session):
    """The crypto sleeve is declarative, not add-only.

    Raising the turnover floor (or a coin's volume falling below it) must
    remove it from the active watchlist. Observed live: a manual prune of
    248 thin pairs was silently reverted wholesale by a later install,
    because install only ever added and re-activated.
    """
    from datetime import datetime, timezone

    from meridian_v3.storage.schema import WatchItem
    from meridian_v3.universe.nse_bse import install_universe

    now = datetime.now(timezone.utc).replace(tzinfo=None)
    # A thin coin that is NOT in the static BINANCE_UNIVERSE fallback.
    session.add(
        WatchItem(symbol="DUSTCOINUSDT", asset_class="crypto", status="active",
                  notes="thin", created_at=now, updated_at=now)
    )
    # A hand-deactivated equity must stay deactivated — this rule is scoped
    # to the dynamic crypto sleeve only.
    session.add(
        WatchItem(symbol="RELIANCE", asset_class="equity", status="inactive",
                  notes="user turned this off", created_at=now, updated_at=now)
    )
    session.flush()

    install_universe(session)
    session.flush()

    dust = session.query(WatchItem).filter_by(symbol="DUSTCOINUSDT").one()
    assert dust.status == "inactive", "a coin outside the universe must be deactivated"

    # BTCUSDT is in the static sleeve, so it must be active.
    btc = session.query(WatchItem).filter_by(symbol="BTCUSDT").one()
    assert btc.status == "active"


def test_pegged_assets_are_excluded_however_liquid_they_are(monkeypatch):
    """A stablecoin cannot clear a cost hurdle it is definitionally unable to
    reach, so turnover alone is the wrong filter.

    Observed live: USDCUSDT and RLUSDUSDT are among the highest-volume pairs
    Binance lists, sailed through the turnover cut, and the desk opened
    Rs35,735 of them -- 36% of the book -- in instruments that move ~0.014%
    a day against a ~7.6% hurdle.
    """
    import httpx

    monkeypatch.setattr(
        binance_mod, "_spot_roots_cache", {"USDCUSDT", "BTCUSDT", "WILDUSDT"}
    )

    class _Res:
        def raise_for_status(self):
            pass

        def json(self):
            return [
                # Enormous volume, no movement: a peg.
                {"symbol": "USDCUSDT", "quoteVolume": "900000000", "highPrice": "1.0002", "lowPrice": "1.0000"},
                # A quiet day for a major must still qualify.
                {"symbol": "BTCUSDT", "quoteVolume": "500000000", "highPrice": "100363", "lowPrice": "100000"},
                {"symbol": "WILDUSDT", "quoteVolume": "20000000", "highPrice": "1.5", "lowPrice": "1.0"},
            ]

    monkeypatch.setattr(httpx, "get", lambda *a, **k: _Res())
    kept = {s for s, _ in binance_mod.liquid_usdt_pairs(min_quote_volume=5_000_000)}
    assert "USDCUSDT" not in kept, "a peg must never enter the universe"
    assert "BTCUSDT" in kept, "a calm day for BTC must not exclude it"
    assert "WILDUSDT" in kept
