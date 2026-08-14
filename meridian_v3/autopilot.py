"""Paper autopilot.

While the desk is open and paper-auto is on, this loop:

  1. refreshes the tape now and then
  2. exits paper clips (stop, target, opposite signal, near the close)
  3. writes those results into the Bayesian trainer
  4. runs a new decision cycle (paper only unless live is armed)

Live never starts itself. One click of Seed turns this on.
"""

from __future__ import annotations

import threading
import time
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from meridian_v3.config import get_settings
from meridian_v3.engine.meta_label import primary_direction, rsi
from meridian_v3.execution.brokers.paper_broker import PaperBroker
from meridian_v3.execution.oms import OrderManager
from meridian_v3.pipeline import mark_to_market, persist_belief, run_cycle
from meridian_v3.safety.guards import session_state
from meridian_v3.storage.db import get_session
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

    exits = manage_exits(session, in_session=in_session, minutes_to_close=minutes)
    result = run_cycle(session, live_armed=False)
    mark_to_market(session, "paper")
    paper = session.scalar(select(AccountState).where(AccountState.venue == "paper"))
    assert paper is not None
    session_bit = "market open" if in_session else "using last close — market is shut, paper still learns"
    note = (
        f"{session_bit}. "
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


def manage_exits(session: Session, *, in_session: bool, minutes_to_close: int) -> dict:
    settings = get_settings()
    paper = session.scalar(select(AccountState).where(AccountState.venue == "paper"))
    if paper is None:
        return {"closed": 0}
    broker = PaperBroker(cash=paper.cash)
    oms = OrderManager(session, broker)
    closed = 0
    rows = list(session.scalars(select(Position).where(Position.venue == "paper", Position.status == "open")))
    flatten = in_session and minutes_to_close <= settings.safety.flatten_before_close_minutes
    for pos in rows:
        cache = session.scalar(select(PriceCache).where(PriceCache.symbol == pos.symbol))
        if cache is None or not cache.last:
            continue
        last = cache.last
        reason = _exit_reason(pos, last, cache, flatten=flatten, session=session)
        if not reason:
            continue
        out = oms.close_position(pos, price=last, reason=reason)
        if out.get("ok"):
            persist_belief(session, won=float(out.get("pnl") or 0) > 0)
            closed += 1
    return {"closed": closed}


def _exit_reason(pos: Position, last: float, cache, *, flatten: bool, session: Session) -> str:
    if flatten and pos.horizon == "intraday":
        return "End of day — flattening the same-day paper clip."
    if pos.side == "buy" and pos.stop and last <= pos.stop:
        return f"Stop hit at ₹{last:,.2f} (line was ₹{pos.stop:,.2f})."
    if pos.side == "sell" and pos.stop and last >= pos.stop:
        return f"Stop hit at ₹{last:,.2f} (line was ₹{pos.stop:,.2f})."
    if pos.side == "buy" and pos.stop and pos.stop < pos.avg_price:
        target = pos.avg_price + 2.0 * (pos.avg_price - pos.stop)
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
    while not _stop.is_set():
        session = get_session()
        try:
            paper = session.scalar(select(AccountState).where(AccountState.venue == "paper"))
            if paper is not None and paper.paper_auto:
                tick(session)
                session.commit()
            else:
                session.rollback()
        except Exception as exc:  # noqa: BLE001 — worker must stay up
            session.rollback()
            _last_error = str(exc)
        finally:
            session.close()
        _stop.wait(max(15, settings.alerts.poll_seconds))
