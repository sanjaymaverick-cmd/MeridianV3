"""Part 3 item 3 — max rupee loss per day kill switch.

Two things are tested here:

  1. ``evaluate_safety()`` actually trips ``allow_paper``/``allow_live`` False
     once the day's rupee loss clears the configured cap (and leaves them
     alone otherwise).
  2. The previously-dead ``SafetyVerdict.allow_paper`` wiring: before this
     change, ``evaluate_safety()`` always set ``allow_paper = True``
     unconditionally and nothing downstream ever read it — ``decide()``'s
     ``paper = ...`` line never referenced ``safety.allow_paper`` at all.
     ``test_decide_forces_paper_false_when_daily_loss_cap_breached`` is the
     one that proves that's fixed: same signal, same everything, except
     ``daily_loss_inr`` crosses the cap, and ``paper`` flips from True to
     False purely because of the (now-enforced) safety verdict.

Also covers the ``_day_start_equity`` helper (pipeline.py) that supplies the
real ``daily_loss_inr`` number to a live cycle.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from meridian_v3.config import Settings, get_settings
from meridian_v3.decision.engine import DecisionInput, decide
from meridian_v3.engine.confluence import FactorVote
from meridian_v3.engine.drawdown import assess_drawdown
from meridian_v3.engine.edge import CostEstimate
from meridian_v3.engine.meta_label import PrimarySignal
from meridian_v3.pipeline import _day_start_equity, run_cycle
from meridian_v3.safety.guards import evaluate_safety
from meridian_v3.storage.schema import AccountState, EquityPoint
from meridian_v3.storage.seed import seed_demo


def _now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _market_open_now():
    # A fixed Monday within NSE hours (09:15-15:30 IST), so the session-block
    # branch of evaluate_safety() never fires and only the thing under test
    # (the daily-loss kill switch) affects the verdict.
    return datetime(2026, 8, 17, 11, 0, tzinfo=ZoneInfo("Asia/Kolkata"))


# ---------------------------------------------------------------------------
# evaluate_safety() unit tests
# ---------------------------------------------------------------------------


def test_daily_loss_below_cap_does_not_touch_the_verdict():
    settings = Settings()
    v_without = evaluate_safety(
        drawdown=assess_drawdown(50_000, 50_000),
        settings=settings,
        live_armed=True,
        live_today=0,
        confidence=0.9,
        market="equity_cash",
        horizon="intraday",
        now=_market_open_now(),
        daily_loss_inr=0.0,
    )
    v_below_cap = evaluate_safety(
        drawdown=assess_drawdown(50_000, 50_000),
        settings=settings,
        live_armed=True,
        live_today=0,
        confidence=0.9,
        market="equity_cash",
        horizon="intraday",
        now=_market_open_now(),
        daily_loss_inr=settings.safety.max_daily_loss_inr - 1.0,
    )
    assert v_without.allow_paper and v_without.allow_live
    # Same verdict shape below the cap — the kill switch hasn't tripped yet.
    assert v_below_cap.allow_paper == v_without.allow_paper
    assert v_below_cap.allow_live == v_without.allow_live


def test_daily_loss_at_cap_blocks_both_paper_and_live():
    settings = Settings()
    v = evaluate_safety(
        drawdown=assess_drawdown(50_000, 50_000),
        settings=settings,
        live_armed=True,
        live_today=0,
        confidence=0.9,
        market="equity_cash",
        horizon="intraday",
        now=_market_open_now(),
        daily_loss_inr=settings.safety.max_daily_loss_inr,
    )
    assert v.allow_paper is False
    assert v.allow_live is False
    assert any("daily loss" in r.lower() for r in v.reasons)
    assert any(f"{settings.safety.max_daily_loss_inr:,.0f}" in r for r in v.reasons)


def test_daily_loss_above_cap_also_blocks():
    settings = Settings()
    v = evaluate_safety(
        drawdown=assess_drawdown(50_000, 50_000),
        settings=settings,
        live_armed=True,
        live_today=0,
        confidence=0.9,
        market="equity_cash",
        horizon="intraday",
        now=_market_open_now(),
        daily_loss_inr=settings.safety.max_daily_loss_inr * 3,
    )
    assert v.allow_paper is False
    assert v.allow_live is False


# ---------------------------------------------------------------------------
# decide() end-to-end — proves allow_paper is actually enforced now
# ---------------------------------------------------------------------------


def _input(**kwargs):
    base = dict(
        symbol="INFY",
        price=1400,
        atr=20,
        # Pinned to market hours, like the evaluate_safety tests above: new
        # paper entries are blocked in a shut session, so a wall-clock
        # `now()` here made the test's outcome depend on the day it ran.
        created_at=_market_open_now(),
        primary=PrimarySignal(1, 1.4, "trend is up"),
        votes=[
            FactorVote("trend", 0.8, 1.0, "up"),
            FactorVote("breakout", 0.6, 0.8, "high"),
            FactorVote("score", 0.5, 0.7, "quality"),
        ],
        win_rupees=80,
        loss_rupees=30,
        costs=CostEstimate(1, 1, 1, 1),
        payoff=2.0,
        equity=50_000,
        cash=50_000,
        drawdown=assess_drawdown(50_000, 50_000),
        live_armed=False,
        live_today=0,
        open_count=0,
        daily_loss_inr=0.0,
        now=_market_open_now(),
    )
    base.update(kwargs)
    return DecisionInput(**base)


def test_decide_writes_paper_when_daily_loss_is_small():
    d = decide(_input(daily_loss_inr=0.0), Settings())
    assert d.action == "buy"
    assert d.paper is True
    assert d.safety.allow_paper is True


def test_decide_forces_paper_false_when_daily_loss_cap_breached():
    """The test that proves the previously-dead allow_paper wiring now bites.

    Identical DecisionInput to the "paper is written" case above, except
    ``daily_loss_inr`` has crossed ``settings.safety.max_daily_loss_inr``.
    Before this change, ``evaluate_safety()`` always returned
    ``allow_paper=True`` and ``decide()`` never read it, so this same signal
    would still have gone to paper. Now it must not.
    """
    settings = Settings()
    d = decide(_input(daily_loss_inr=settings.safety.max_daily_loss_inr), settings)
    assert d.action == "buy"  # the underlying signal is unchanged...
    assert d.safety.allow_paper is False  # ...but the kill switch tripped...
    assert d.paper is False  # ...and paper is now correctly suppressed.
    assert d.live is False


# ---------------------------------------------------------------------------
# _day_start_equity() — the three fallback cases
# ---------------------------------------------------------------------------


def test_day_start_equity_uses_earliest_point_today(session):
    session.add(AccountState(venue="paper", cash=50_000, equity=48_000, peak=50_000, updated_at=_now()))
    now = datetime(2026, 8, 17, 10, 0)  # naive UTC -> 15:30 IST, same IST day
    day_start_utc = datetime(2026, 8, 16, 18, 30)  # IST midnight Aug 17 in UTC
    # Two points "today" (IST) — earliest should win.
    session.add(EquityPoint(venue="paper", as_of=day_start_utc + timedelta(hours=1), equity=48_500, cash=48_500, peak=50_000))
    session.add(EquityPoint(venue="paper", as_of=day_start_utc + timedelta(hours=4), equity=47_500, cash=47_500, peak=50_000))
    # A point from "yesterday" (IST) should be ignored when today has data.
    session.add(EquityPoint(venue="paper", as_of=day_start_utc - timedelta(hours=6), equity=50_000, cash=50_000, peak=50_000))
    session.flush()
    assert _day_start_equity(session, "paper", now) == 48_500


def test_day_start_equity_falls_back_to_yesterdays_last_point(session):
    session.add(AccountState(venue="paper", cash=50_500, equity=50_500, peak=50_500, updated_at=_now()))
    now = datetime(2026, 8, 17, 10, 0)
    day_start_utc = datetime(2026, 8, 16, 18, 30)
    # Nothing today. Two points yesterday — most recent (closest to today) wins.
    session.add(EquityPoint(venue="paper", as_of=day_start_utc - timedelta(hours=8), equity=49_000, cash=49_000, peak=50_000))
    session.add(EquityPoint(venue="paper", as_of=day_start_utc - timedelta(hours=2), equity=50_500, cash=50_500, peak=50_500))
    session.flush()
    assert _day_start_equity(session, "paper", now) == 50_500


def test_day_start_equity_falls_back_to_account_equity_with_no_history(session):
    session.add(AccountState(venue="paper", cash=42_000, equity=42_000, peak=50_000, updated_at=_now()))
    session.flush()
    now = datetime(2026, 8, 17, 10, 0)
    assert _day_start_equity(session, "paper", now) == 42_000


# ---------------------------------------------------------------------------
# End-to-end through run_cycle — the full pipeline honors the kill switch
# ---------------------------------------------------------------------------


def test_run_cycle_opens_no_new_paper_once_daily_loss_cap_is_breached(session):
    seed_demo(session, reset=True)
    settings = get_settings()
    now = _now()

    # Seeding wrote a same-day EquityPoint at the starting equity. Simulate a
    # big loss since then by dropping the live paper equity/cash directly —
    # this crosses the (default ₹2,000) daily loss cap.
    paper = session.query(AccountState).filter_by(venue="paper").one()
    starting = paper.equity
    paper.cash = starting - (settings.safety.max_daily_loss_inr + 500)
    paper.equity = starting - (settings.safety.max_daily_loss_inr + 500)
    session.flush()

    result = run_cycle(session, live_armed=False, now=now)
    assert result["paper_opened"] == 0
