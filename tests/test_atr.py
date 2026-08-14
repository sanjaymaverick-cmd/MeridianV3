from meridian_v3.engine.atr import atr_quantity, average_true_range, true_range


def test_true_range():
    assert true_range(12, 10, 11) == 2


def test_atr_quantity_respects_cash():
    plan = atr_quantity(risk_rupees=100, atr=10, price=500, cash=500, lot_step=1)
    assert plan.qty == 1
    assert plan.notional == 500


def test_average_true_range_runs():
    highs = [11, 12, 13, 12, 14]
    lows = [10, 10, 11, 10, 12]
    closes = [10.5, 11.5, 12, 11, 13]
    atr = average_true_range(highs, lows, closes, period=3)
    assert atr > 0
