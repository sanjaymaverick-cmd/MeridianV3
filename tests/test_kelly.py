from meridian_v3.engine.kelly import confidence_weighted_fractional_kelly, full_kelly


def test_full_kelly_positive_edge():
    # p=0.6, b=1 → f* = 0.2
    assert abs(full_kelly(0.6, 1.0) - 0.2) < 1e-9


def test_negative_edge_is_zero_size():
    result = confidence_weighted_fractional_kelly(p=0.4, b=1.0, confidence=0.9)
    assert result.sized == 0.0


def test_fraction_and_confidence_shrink():
    result = confidence_weighted_fractional_kelly(p=0.6, b=1.0, confidence=0.5, kappa=0.15)
    assert 0 < result.sized < result.full_kelly
    assert abs(result.sized - 0.15 * 0.5 * 0.2) < 1e-9
