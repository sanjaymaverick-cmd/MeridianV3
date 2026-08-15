"""2.2 — a mark converted through the hardcoded USDINR fallback must be
flagged quality="fx_fallback", never silently presented as "live"."""

from datetime import datetime, timezone

import pandas as pd

from meridian_v3.data_providers.service import _apply_frame, _to_inr, _usdinr
from meridian_v3.storage.schema import PriceCache


def _now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _ohlc_frame():
    idx = pd.date_range("2026-08-01", periods=6, freq="D")
    return pd.DataFrame(
        {
            "Open": [100.0] * 6,
            "High": [101.0] * 6,
            "Low": [99.0] * 6,
            "Close": [100.0, 100.5, 101.0, 100.8, 101.2, 101.5],
            "Volume": [1000.0] * 6,
        },
        index=idx,
    )


def test_usdinr_missing_cache_is_the_fallback(session):
    rate, is_fallback = _usdinr(session)
    assert is_fallback is True
    assert rate == 83.5


def test_usdinr_live_cache_is_not_the_fallback(session):
    session.add(PriceCache(symbol="USDINR", last=87.0, quality="live"))
    session.flush()
    rate, is_fallback = _usdinr(session)
    assert is_fallback is False
    assert rate == 87.0


def test_commodity_mark_is_flagged_fx_fallback_when_usdinr_missing(session):
    """No USDINR cache ⇒ GOLD.X (converted via the USDINR fallback) comes out
    quality="fx_fallback", not "live"."""
    hist = _ohlc_frame()
    hist, quality_override = _to_inr(session, "GOLD.X", hist)
    assert quality_override == "fx_fallback"
    assert _apply_frame(session, "GOLD.X", hist, _now(), quality_override=quality_override)
    cache = session.query(PriceCache).filter_by(symbol="GOLD.X").one()
    assert cache.quality == "fx_fallback"


def test_commodity_mark_is_live_when_usdinr_present(session):
    """Positive control: a real USDINR cache means no fallback flag at all."""
    session.add(PriceCache(symbol="USDINR", last=87.0, quality="live"))
    session.flush()
    hist = _ohlc_frame()
    hist, quality_override = _to_inr(session, "GOLD.X", hist)
    assert quality_override is None
    assert _apply_frame(session, "GOLD.X", hist, _now(), quality_override=quality_override)
    cache = session.query(PriceCache).filter_by(symbol="GOLD.X").one()
    assert cache.quality == "live"


def test_fx_cross_mark_is_flagged_fx_fallback_when_usdinr_stale(session):
    """A stale/zero USDINR row (fails the >50 sanity check) trips the same
    flag on a cross-FX pair (EURUSD, the usd_quote conversion path)."""
    session.add(PriceCache(symbol="USDINR", last=0.0, quality="missing"))
    session.flush()
    hist = _ohlc_frame()
    hist, quality_override = _to_inr(session, "EURUSD", hist)
    assert quality_override == "fx_fallback"
    assert _apply_frame(session, "EURUSD", hist, _now(), quality_override=quality_override)
    cache = session.query(PriceCache).filter_by(symbol="EURUSD").one()
    assert cache.quality == "fx_fallback"
