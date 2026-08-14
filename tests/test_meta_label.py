from meridian_v3.engine.bayesian import start_belief, update_belief
from meridian_v3.engine.confluence import FactorVote, score_confluence
from meridian_v3.engine.freshness import freshness
from meridian_v3.engine.meta_label import OnlineLogit, PrimarySignal, meta_label
from meridian_v3.engine.walkforward import robustness, walk_forward_folds
from datetime import datetime, timedelta, timezone


def test_meta_label_skips_flat():
    out = meta_label(PrimarySignal(0, 0, "flat"), {"x": 1})
    assert out.take is False


def test_cold_start_takes_a_clear_setup():
    from meridian_v3.engine.meta_label import default_features

    feats = default_features(
        confluence=78, freshness=1.0, atr_pct=0.015, regime_fit=0.6, primary_score=1.4, cost_pct=0.002
    )
    out = meta_label(PrimarySignal(1, 1.4, "trend is up"), feats, min_p=0.52)
    assert out.take is True
    assert out.p_success > 0.52


def test_online_logit_learns():
    model = OnlineLogit(lr=0.2)
    feats = {"confluence": 0.8, "freshness": 1.0}
    for _ in range(12):
        model.update(feats, True)
    assert model.predict(feats) > 0.6


def test_belief_updates():
    b = start_belief()
    b = update_belief(b, True)
    b = update_belief(b, True)
    assert b.wins == 2
    assert b.mean > 0.5


def test_confluence_range():
    c = score_confluence([FactorVote("a", 1, 1), FactorVote("b", 1, 1)])
    assert 50 < c.score <= 100
    assert c.side == 1


def test_freshness_decays():
    now = datetime.now(timezone.utc)
    old = freshness(now - timedelta(hours=12), now, half_life_hours=6, floor=0.35)
    assert old.stale
    assert old.value < 0.35


def test_walkforward_rejects_curve_fit():
    folds = walk_forward_folds(200, train=60, test=20, embargo=2)
    assert folds
    report = robustness([0.4, 0.5], [-0.1, -0.05])
    assert report.robust is False
