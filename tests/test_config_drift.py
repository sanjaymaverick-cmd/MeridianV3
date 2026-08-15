"""Part 3 item 8 -- config-drift guardrail.

Phase 2 (item 2.8 of the fix plan) reconciled every stale reference to the
old ₹5,000 book against the real ₹50,000 book, and fixed stale concurrency
numbers in the docs. This test exists to catch that exact class of
regression the moment it happens again, by checking that a short, explicit
list of headline numeric claims in README.md / docs/*.md still agree with
`config/default.yaml` (via `get_settings()`).

Deliberately NOT a general markdown-number-extraction engine -- just a
handful of explicit (doc file, expected string, config value) checks for
the highest-value numbers: starting equity (the one that actually
regressed before), concurrency limits, drawdown-pause percentage, and the
cash reserve amount those two combine to imply.
"""

from __future__ import annotations

from pathlib import Path

from meridian_v3.config import ROOT, get_settings

README = ROOT / "README.md"
SAFETY_DOC = ROOT / "docs" / "07-safety.md"
SIZING_DOC = ROOT / "docs" / "03-capital-sizing.md"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_readme_states_current_starting_equity():
    settings = get_settings()
    text = _read(README)
    current = f"₹{settings.account.starting_equity_inr:,.0f}"
    assert current == "₹50,000"  # sanity: this is the figure the plan says regressed before
    assert current in text, f"README.md no longer mentions the current starting equity {current}"
    # Guard against the exact old figure creeping back in as a "starting equity" claim.
    assert "₹5,000" not in text, "README.md mentions the stale ₹5,000 starting-equity figure"


def test_capital_sizing_doc_states_current_starting_equity():
    settings = get_settings()
    text = _read(SIZING_DOC)
    current = f"₹{settings.account.starting_equity_inr:,.0f}"
    assert current in text, (
        f"docs/03-capital-sizing.md no longer mentions the current starting equity {current}"
    )


def test_safety_doc_concurrency_matches_config():
    settings = get_settings()
    text = _read(SAFETY_DOC)
    expected = f"{settings.sizing.max_concurrent_normal} / {settings.sizing.max_concurrent_high}"
    assert expected == "16 / 20"  # sanity: matches config/default.yaml today
    assert expected in text, (
        f"docs/07-safety.md's concurrency figures no longer match "
        f"sizing.max_concurrent_normal/max_concurrent_high ({expected})"
    )


def test_safety_doc_drawdown_pause_matches_config():
    settings = get_settings()
    text = _read(SAFETY_DOC)
    expected = f"{settings.safety.drawdown_pause_pct * 100:.0f}%"
    assert expected == "20%"  # sanity: matches config/default.yaml today
    assert expected in text, (
        f"docs/07-safety.md's drawdown-pause percentage no longer matches "
        f"safety.drawdown_pause_pct ({expected})"
    )


def test_safety_doc_cash_reserve_matches_config():
    """The doc's "never spend the last ₹5,000 of a ₹50,000 book" line is a
    derived figure (cash_reserve_pct * starting_equity_inr), not a
    leftover from the old ₹5,000-book bug -- check the derivation directly
    so it can't silently drift from either input."""
    settings = get_settings()
    text = _read(SAFETY_DOC)
    reserve_amount = settings.sizing.cash_reserve_pct * settings.account.starting_equity_inr
    expected = f"₹{reserve_amount:,.0f}"
    assert expected == "₹5,000"  # sanity: 10% of ₹50,000 today
    assert expected in text, (
        f"docs/07-safety.md's cash-reserve figure no longer matches "
        f"sizing.cash_reserve_pct * account.starting_equity_inr ({expected})"
    )
