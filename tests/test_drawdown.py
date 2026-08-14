from meridian_v3.engine.drawdown import assess_drawdown, risk_scale


def test_pause_at_twenty():
    state = assess_drawdown(4000, 5000)
    assert state.live_paused
    assert state.scale == 0
    assert "paused" in state.reason.lower()


def test_scale_between_bands():
    assert risk_scale(0.0) == 1.0
    assert 0 < risk_scale(0.14) < 1
    assert risk_scale(0.20) == 0.0
