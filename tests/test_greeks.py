from datetime import date

from meridian_v3.risk.gamma_scalp import explain_scalp
from meridian_v3.risk.greeks_book import snapshot_from_legs
from meridian_v3.risk.vega_engine import OptionGreekLeg, VegaPolicy
from meridian_v3.risk.vega_strategies import HedgeUnit, plan_vega_actions


def _leg(**kwargs):
    base = dict(
        leg_id="x", symbol="NIFTY", contract_label="CE", lots=2, multiplier=75,
        mark_inr=200, delta=0.5, gamma=0.02, vega_per_lot=110000, theta_per_lot=-2400,
    )
    base.update(kwargs)
    return OptionGreekLeg(**base)


def test_long_gamma_helps():
    snap = snapshot_from_legs("NIFTY", [_leg()], date(2026, 8, 14))
    assert snap.long_gamma
    assert snap.scalp_helps
    assert snap.daily_pnl == snap.theta
    assert snap.gamma_scalp_pnl > 0


def test_short_gamma_hurts():
    snap = snapshot_from_legs("NIFTY", [_leg(gamma=-0.02)], date(2026, 8, 14))
    assert snap.short_gamma
    assert snap.gamma_scalp_pnl < 0
    report = explain_scalp(snap)
    assert report.hurts
    assert "not a harvest" in report.suggestion.lower()


def test_six_vega_actions():
    snap = snapshot_from_legs("NIFTY", [_leg()], date(2026, 8, 14))
    actions = plan_vega_actions(
        snap,
        VegaPolicy(symbol="NIFTY", vega_limit=100000),
        HedgeUnit(vega=55000, delta=0.48, gamma=0.02, theta=-40),
    )
    assert [a.key for a in actions] == [
        "limit_cap", "cut_options", "hedge_options", "book_balance", "regime_limit", "time_reduce",
    ]
