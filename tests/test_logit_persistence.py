"""1.1 — the online meta-label logistic must actually learn in production.

Before this fix, `pipeline.py` never set `DecisionInput.logit`, so
`meta_label()` built a brand-new, zero-`updates` `OnlineLogit()` on every
call — `p_success` was always the fixed `_cold_start_p` formula (F4). These
tests exercise the real persistence path (`persist_logit_update` /
`load_logit` in `pipeline.py`, backed by the new `logit_weights` table) and
assert `p_success` for the same feature vector measurably changes once real
outcomes go through it.
"""

from __future__ import annotations

from meridian_v3.engine.meta_label import PrimarySignal, default_features, meta_label
from meridian_v3.pipeline import load_logit, persist_logit_update
from meridian_v3.storage.schema import LogitWeight


def _features():
    return default_features(
        confluence=80, freshness=1.0, atr_pct=0.02, regime_fit=0.6, primary_score=1.5, cost_pct=0.002
    )


def test_untrained_logit_falls_back_to_cold_start(session):
    model = load_logit(session, "core")
    assert model.trained is False
    assert model.updates == 0


def test_p_success_moves_after_real_outcomes_persist(session):
    features = _features()
    primary = PrimarySignal(direction=1, raw_score=1.5, reason="test setup")

    before_model = load_logit(session, "core")
    before = meta_label(primary, features, before_model, min_p=0.0).p_success

    # Same real path autopilot.py calls when a paper clip closes: load the
    # persisted model, .update() it on the actual outcome, save it back.
    for _ in range(15):
        persist_logit_update(session, features, won=False, rule="core")

    after_model = load_logit(session, "core")
    after = meta_label(primary, features, after_model, min_p=0.0).p_success

    assert after_model.trained
    assert after_model.updates == 15
    assert after != before
    assert after < before  # trained on fifteen straight losses on this exact feature vector


def test_weights_survive_a_reload(session):
    features = _features()
    persist_logit_update(session, features, won=True, rule="core")
    persist_logit_update(session, features, won=True, rule="core")

    reloaded = load_logit(session, "core")
    assert reloaded.trained
    assert reloaded.updates == 2
    # Bias plus every real feature key got its own persisted row.
    rows = session.query(LogitWeight).filter_by(rule_name="core").all()
    persisted_features = {row.feature for row in rows}
    assert "__bias__" in persisted_features
    assert set(features).issubset(persisted_features)


def test_rules_do_not_cross_contaminate(session):
    features = _features()
    for _ in range(5):
        persist_logit_update(session, features, won=True, rule="core")

    other = load_logit(session, "some_other_rule")
    assert other.trained is False
    assert other.updates == 0
