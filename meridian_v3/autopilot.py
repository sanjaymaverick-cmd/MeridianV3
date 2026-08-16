"""Paper autopilot.

While paper-auto is on, this loop:

  1. refreshes the tape now and then
  2. exits paper clips (stop, target, opposite signal, or that market's own close)
  3. writes those results into the Bayesian trainer
  4. runs a new decision cycle (paper only unless live is armed)

India cash flattening uses 15:30 IST. Crypto is never flattened for
session. FX and global commodities use their Sunday–Friday clocks.

Live never starts itself. One click of Seed turns this on.
"""

from __future__ import annotations

import json
import threading
import time
from datetime import datetime, timezone

from loguru import logger
from sqlalchemy import select
from sqlalchemy.orm import Session

from meridian_v3.config import get_settings
from meridian_v3.alerts.notify import emit_alert
from meridian_v3.capital.sizer import stop_price
from meridian_v3.engine.meta_label import primary_direction, rsi
from meridian_v3.execution.brokers.paper_broker import PaperBroker
from meridian_v3.execution.oms import OrderManager
from meridian_v3.pipeline import mark_to_market, persist_belief, persist_logit_update, run_cycle
from meridian_v3.router.calendar import (
    INDIA_WEEKEND_FLATTEN,
    coverage_note,
    india_session,
    is_india_market,
    market_session,
)
from meridian_v3.safety.guards import session_state
from meridian_v3.storage.db import desk_lock, get_session
from meridian_v3.storage.schema import AccountState, Position, PriceBar, PriceCache

_lock = threading.Lock()
_thread: threading.Thread | None = None
_stop = threading.Event()
_ticks = 0
_last_error = ""


def is_running() -> bool:
    return _thread is not None and _thread.is_alive()


def last_error() -> str:
    return _last_error


def start_autopilot() -> None:
    global _thread
    with _lock:
        if _thread is not None and _thread.is_alive():
            return
        _stop.clear()
        _thread = threading.Thread(target=_loop, name="meridian-paper-auto", daemon=True)
        _thread.start()


def stop_autopilot() -> None:
    _stop.set()


def set_paper_auto(session: Session, on: bool) -> None:
    paper = session.scalar(select(AccountState).where(AccountState.venue == "paper"))
    if paper is None:
        return
    paper.paper_auto = 1 if on else 0
    paper.updated_at = datetime.now(timezone.utc).replace(tzinfo=None)
    if on:
        start_autopilot()


def tick(session: Session, *, refresh_prices: bool | None = None) -> dict:
    """One autonomous pass. Safe to call from tests or the worker."""
    global _ticks, _last_error
    settings = get_settings()
    # Tests call tick() without the worker lock. The worker holds desk_lock.
    paper = session.scalar(select(AccountState).where(AccountState.venue == "paper"))
    if paper is None:
        return {"ok": False, "note": "desk is not seeded"}
    if not paper.paper_auto:
        note = "Paper auto is off. Click Start paper auto, or Seed demo desk."
        paper.last_cycle_note = note
        return {"ok": False, "note": note}

    now = datetime.now(timezone.utc)
    in_session, minutes = session_state(now, settings)
    _ticks += 1
    if refresh_prices is None:
        refresh_prices = _ticks == 1 or _ticks % max(settings.alerts.price_every_cycles, 1) == 0
    price_note = ""
    if refresh_prices:
        from meridian_v3.data_providers.service import PriceProvider

        priced = PriceProvider(session).refresh()
        price_note = f"tape {priced.get('marked', 0)} ok"
        if priced.get("failed"):
            price_note += f", {priced['failed']} missing"

    exits = manage_exits(session, in_session=in_session, minutes_to_close=minutes, now=now)
    result = run_cycle(session, live_armed=False)
    mark_to_market(session, "paper")
    paper = session.scalar(select(AccountState).where(AccountState.venue == "paper"))
    assert paper is not None
    session_bit = coverage_note(now, settings)
    india_bit = ""
    if exits.get("india_closed"):
        india_bit = (
            f" Closed {exits['india_closed']} India clip(s) "
            f"(₹{exits.get('india_freed', 0):,.0f} back to paper). "
        )
    note = (
        f"{session_bit}. {india_bit}"
        f"Exits {exits['closed']}. New paper fills {result['paper_opened']}. "
        f"Hold {result.get('holds', 0)}."
    )
    if price_note:
        note = f"{price_note}. {note}"
    paper.last_cycle_at = datetime.now(timezone.utc).replace(tzinfo=None)
    paper.last_cycle_note = note
    _last_error = ""
    return {
        "ok": True,
        "note": note,
        "paper_opened": result["paper_opened"],
        "exits": exits["closed"],
        "in_session": in_session,
    }


def manage_exits(
    session: Session,
    *,
    in_session: bool,
    minutes_to_close: int,
    now: datetime | None = None,
) -> dict:
    settings = get_settings()
    paper = session.scalar(select(AccountState).where(AccountState.venue == "paper"))
    if paper is None:
        return {"closed": 0, "india_closed": 0, "india_freed": 0.0, "symbols": []}
    now = now or datetime.now(timezone.utc)
    cash_before = float(paper.cash)
    broker = PaperBroker(cash=paper.cash)
    oms = OrderManager(session, broker)
    closed = 0
    india_closed = 0
    symbols: list[str] = []
    rows = list(session.scalars(select(Position).where(Position.venue == "paper", Position.status == "open")))
    india = india_session(now, settings)
    india_dark = not india.in_session
    india_eod = (
        india.in_session
        and india.minutes_to_close <= settings.safety.flatten_before_close_minutes
    )
    for pos in rows:
        cache = session.scalar(select(PriceCache).where(PriceCache.symbol == pos.symbol))
        last = None
        if cache is not None and cache.last:
            last = cache.last
        elif pos.avg_price:
            last = pos.avg_price
        if not last:
            continue
        weekend_india = india_dark and is_india_market(pos.market)
        flatten = False
        if weekend_india:
            flatten = True
        elif is_india_market(pos.market) and india_eod:
            flatten = True
        else:
            clock = market_session(now, settings, pos.market or "equity_cash")
            flatten = (
                (not clock.always_on)
                and clock.in_session
                and clock.minutes_to_close <= settings.safety.flatten_before_close_minutes
            )
        reason = _exit_reason(
            pos,
            last,
            cache,
            flatten=flatten,
            session=session,
            weekend_india=weekend_india,
            settings=settings,
        )
        if not reason:
            continue
        entry_avg = float(pos.avg_price or 0.0)
        out = oms.close_position(pos, price=last, reason=reason)
        if out.get("ok"):
            # A close with no real price move is a cost-only scratch, not a
            # directional outcome — it must not train the belief or logit,
            # whatever the exit reason claims happened.
            if not _is_costonly_close(reason, entry_avg, last, settings):
                _train_from_close(session, pos, won=float(out.get("pnl") or 0) > 0)
            closed += 1
            symbols.append(pos.symbol)
            if is_india_market(pos.market):
                india_closed += 1
    paper = session.scalar(select(AccountState).where(AccountState.venue == "paper"))
    freed = max(0.0, float(paper.cash) - cash_before) if paper is not None else 0.0
    return {
        "closed": closed,
        "india_closed": india_closed,
        "india_freed": freed,
        "symbols": symbols,
    }


def flatten_india_paper(
    session: Session,
    *,
    now: datetime | None = None,
    force: bool = False,
) -> dict:
    """Close every open India paper clip and credit the cash back.

    Used on the Friday 15:30 IST → Monday 09:15 IST gap so the book can
    work crypto / FX / commodities. ``force`` closes even if NSE is open.
    """
    settings = get_settings()
    now = now or datetime.now(timezone.utc)
    india = india_session(now, settings)
    if not force and india.in_session and india.minutes_to_close > settings.safety.flatten_before_close_minutes:
        return {"closed": 0, "india_closed": 0, "india_freed": 0.0, "symbols": [], "note": "India is still open."}

    paper = session.scalar(select(AccountState).where(AccountState.venue == "paper"))
    if paper is None:
        return {"closed": 0, "india_closed": 0, "india_freed": 0.0, "symbols": []}
    cash_before = float(paper.cash)
    broker = PaperBroker(cash=paper.cash)
    oms = OrderManager(session, broker)
    symbols: list[str] = []
    rows = list(session.scalars(select(Position).where(Position.venue == "paper", Position.status == "open")))
    for pos in rows:
        if not is_india_market(pos.market):
            continue
        cache = session.scalar(select(PriceCache).where(PriceCache.symbol == pos.symbol))
        last = cache.last if cache is not None and cache.last else pos.avg_price
        if not last:
            continue
        entry_avg = float(pos.avg_price or 0.0)
        out = oms.close_position(pos, price=last, reason=INDIA_WEEKEND_FLATTEN)
        if out.get("ok"):
            if not _is_costonly_close(INDIA_WEEKEND_FLATTEN, entry_avg, last, settings):
                _train_from_close(session, pos, won=float(out.get("pnl") or 0) > 0)
            symbols.append(pos.symbol)
    paper = session.scalar(select(AccountState).where(AccountState.venue == "paper"))
    freed = max(0.0, float(paper.cash) - cash_before) if paper is not None else 0.0
    return {
        "closed": len(symbols),
        "india_closed": len(symbols),
        "india_freed": freed,
        "symbols": symbols,
        "cash": float(paper.cash) if paper is not None else 0.0,
    }


def _train_from_close(session: Session, pos: Position, won: bool) -> None:
    """Feed a genuine paper outcome to both trainers (belief + logistic).

    Called from the same two spots the old belief-only training happened —
    `manage_exits` and `flatten_india_paper` — right after a real (non
    cost-only) exit. 1.1 wires the online logistic in alongside the Beta
    belief so `p_success` actually moves as the book closes clips, instead
    of a fixed cold-start formula forever.

    Part 3 item 4 — the belief is now keyed by ``pos.market`` (the same
    market column every OMS write site populates from ``decision.market``)
    instead of the single global ``"core"`` rule, so an equity outcome no
    longer moves crypto's or futures' belief and vice versa. The online
    logistic (``persist_logit_update`` below) intentionally still uses the
    global ``"core"`` rule — only item 4's belief tracking is in scope here.
    """
    persist_belief(session, won=won, rule=pos.market or "equity_cash")
    try:
        features = json.loads(pos.feature_json or "{}")
    except (TypeError, ValueError):
        features = {}
    if features:
        persist_logit_update(session, features, won)


def _is_costonly_close(reason: str, avg: float, exit_mark: float, settings) -> bool:
    """True for any close where the price never actually moved.

    The label must come from what the price did, not from what the exit
    reason claims. This used to fire only for EOD/weekend flattens, on the
    theory that a stop or target implies real movement. It doesn't: a stop
    line computed off a bad mark fires at the entry price, and the close is
    then recorded as a directional loss despite nothing having happened.

    That distinction is not academic. Of this book's first 107 closed paper
    clips, 101 finished within 0.2% of their entry — fee-only scratches
    mislabelled as outcomes. The online logistic trained on them and learned
    every feature predicts failure (including a *negative* weight on
    confluence, i.e. "more agreement means a worse trade"), which pushed
    p_success under the meta-label's floor and stopped the desk trading at
    all. It could not trade its way out, because it needed trades to unlearn.

    ``reason`` is no longer used to decide this and is kept only so the call
    sites read clearly; a scratch is a scratch however it was closed.
    """
    avg = float(avg or 0.0)
    if avg <= 0:
        return False
    return abs(float(exit_mark) - avg) <= avg * settings.execution.flat_scratch_pct


def _exit_reason(
    pos: Position,
    last: float,
    cache,
    *,
    flatten: bool,
    session: Session,
    weekend_india: bool = False,
    settings=None,
) -> str:
    if weekend_india:
        return INDIA_WEEKEND_FLATTEN
    if flatten and (pos.horizon == "intraday" or is_india_market(pos.market)):
        return "End of day — flattening the same-day paper clip."
    line = stop_price(pos.side, pos.avg_price, pos.stop or 0.0, settings)
    if pos.side == "buy" and line and last <= line:
        return f"Stop hit at ₹{last:,.2f} (line was ₹{line:,.2f})."
    if pos.side == "sell" and line and last >= line:
        return f"Stop hit at ₹{last:,.2f} (line was ₹{line:,.2f})."
    if cache is None:
        return ""
    if pos.side == "buy" and line and line < pos.avg_price:
        target = pos.avg_price + 2.0 * (pos.avg_price - line)
        if last >= target:
            return f"Target hit at ₹{last:,.2f}."
    elif pos.side == "buy" and last >= pos.avg_price * 1.03:
        return f"Target hit at ₹{last:,.2f} (about +3%)."
    bars = list(
        session.scalars(select(PriceBar).where(PriceBar.symbol == pos.symbol).order_by(PriceBar.bar_date.asc()))
    )
    closes = [b.close for b in bars]
    primary = primary_direction(
        last=last,
        sma_fast=cache.sma20,
        sma_slow=cache.sma50,
        rsi=rsi(closes) if closes else None,
        breakout=False,
        mean_revert=False,
        regime="Elevated",
    )
    if pos.side == "buy" and primary.direction < 0:
        return "The tape flipped against the paper long. Closing to train the model."
    if pos.side == "sell" and primary.direction > 0:
        return "The tape flipped against the paper short. Closing to train the model."
    return ""


def _loop() -> None:
    global _last_error
    settings = get_settings()
    # Let the first page load / Seed finish before the worker grabs SQLite.
    _stop.wait(max(8, min(20, settings.alerts.poll_seconds)))
    while not _stop.is_set():
        session = get_session()
        try:
            with desk_lock:
                paper = session.scalar(select(AccountState).where(AccountState.venue == "paper"))
                if paper is not None and paper.paper_auto:
                    tick(session)
                    session.commit()
                else:
                    session.rollback()
        except Exception as exc:  # noqa: BLE001 — worker must stay up
            session.rollback()
            _last_error = str(exc)
            # Part 3 item 6 — the worker's whole point is staying alive, so
            # alerting about its own failure must never become a second way
            # for it to die: this gets its own try/except on top of
            # emit_alert()'s internal webhook guard.
            try:
                emit_alert(session, "worker_error", f"Autopilot worker error: {exc}")
                session.commit()
            except Exception:  # noqa: BLE001 — alerting must never crash the worker
                logger.exception("failed to emit worker_error alert")
                session.rollback()
        finally:
            session.close()
        _stop.wait(max(15, settings.alerts.poll_seconds))
