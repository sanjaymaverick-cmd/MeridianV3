from datetime import date

from meridian_v3.domain.copy import assert_review_copy
from meridian_v3.domain.reviews import compose_decision_review, compose_review
from meridian_v3.risk.greeks_book import snapshot_from_legs
from meridian_v3.risk.vega_engine import OptionGreekLeg
import pytest


def test_review_must_say_not_an_order():
    snap = snapshot_from_legs(
        "GOLD",
        [OptionGreekLeg("1", "GOLD", "CE", 1, 1, 100, 0.4, 0.01, 20000, -100)],
        date(2026, 8, 14),
    )
    review = compose_review(snap)
    assert "(not an order)" in review.title.lower()
    assert_review_copy(review.body)


def test_forbidden_execution_words():
    with pytest.raises(ValueError):
        assert_review_copy("You should hedge this now (not an order)")


def test_decision_review_is_not_a_live_ticket():
    review = compose_decision_review(
        symbol="INFY", action="buy", confidence=0.7, confluence=61, market="equity_cash", paper_only=True
    )
    assert "not an order" in review.title.lower()
    assert "live" not in review.suggestion.lower() or "not armed" in review.suggestion.lower()
