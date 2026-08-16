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


def test_first_update_does_not_throw_away_the_cold_start_prior():
    """`predict` must be continuous across the first update.

    The learned path used to start at bias=0 with no weights, so the moment
    `updates` went 0 -> 1 the model discarded the cold-start prior and fell
    to sigmoid(0) = 0.50. Observed live: a single losing trade took
    p_success from 0.76 to 0.43 -- under the 0.55 meta-label floor -- and
    the desk then could not trade at all, so it could never earn its way
    back. A one-way trapdoor that shut the whole book after one loss.
    """
    from meridian_v3.engine.meta_label import OnlineLogit, _cold_start_p

    feats = {"primary": 1.0, "confluence": 0.72, "freshness": 0.9, "cheap": 1.0}
    model = OnlineLogit()

    # Untrained model agrees exactly with the cold-start formula.
    assert abs(model.predict(feats) - _cold_start_p(feats)) < 1e-9

    before = model.predict(feats)
    model.update(feats, won=False)
    after = model.predict(feats)

    assert after < before, "a loss should still lower confidence"
    # ...but by a learning step, not a cliff. The old behaviour dropped
    # ~0.33 in one update; a single loss must not cross the trading floor.
    assert before - after < 0.10, f"single-update drop of {before - after:.3f} is a cliff"
    assert after > 0.55, "one loss must not block the desk outright"


def test_a_losing_streak_still_shuts_the_desk_down():
    """Graceful degradation is not the same as no degradation -- a genuinely
    bad run must still stop it trading."""
    from meridian_v3.engine.meta_label import OnlineLogit

    feats = {"primary": 1.0, "confluence": 0.72, "freshness": 0.9, "cheap": 1.0}
    model = OnlineLogit()
    for _ in range(6):
        model.update(feats, won=False)
    assert model.predict(feats) < 0.55


def test_wins_can_recover_a_blocked_model():
    """The model must be able to climb back, or any dip is permanent."""
    from meridian_v3.engine.meta_label import OnlineLogit

    feats = {"primary": 1.0, "confluence": 0.72, "freshness": 0.9, "cheap": 1.0}
    model = OnlineLogit()
    for _ in range(6):
        model.update(feats, won=False)
    blocked = model.predict(feats)
    for _ in range(6):
        model.update(feats, won=True)
    assert model.predict(feats) > blocked
