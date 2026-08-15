from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote

from fastapi import APIRouter, File, Form, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from loguru import logger
from sqlalchemy import func, select

from meridian_v3.config import get_settings
from meridian_v3.domain.money import format_inr, format_pct
from meridian_v3.engine.drawdown import assess_drawdown
from meridian_v3.execution.brokers.plugin import get_live_broker
from meridian_v3.ingestion.service import ImportService
from meridian_v3.autopilot import is_running, last_error, set_paper_auto, tick
from meridian_v3.charges.indian import BROKER_LABELS, BROKERS, broker_label, normalize_broker
from meridian_v3.storage.repair import repair_shifted_clips
from meridian_v3.ui.book_view import (
    decorate_fills,
    decorate_positions,
    ensure_fill_charges,
    summarize_charges,
    summarize_closed,
)
from meridian_v3.pipeline import run_cycle
from meridian_v3.storage.db import desk_lock
from meridian_v3.storage.db import get_session
from meridian_v3.storage.schema import (
    AccountState,
    DeskEvent,
    Fill,
    Holding,
    Position,
    RegimeState,
    SignalRow,
    WatchItem,
)
from meridian_v3.storage.seed import seed_demo

TEMPLATES = Jinja2Templates(directory=str(Path(__file__).resolve().parent / "templates"))
TEMPLATES.env.cache = None
TEMPLATES.env.filters["inr"] = format_inr


def _pct_filter(value):
    if value is None:
        return "—"
    shown = value * 100 if abs(value) <= 2 else value
    return format_pct(shown)


TEMPLATES.env.filters["pct"] = _pct_filter

router = APIRouter()


def _q(text: str) -> str:
    return quote(text, safe="")


def _render(request: Request, name: str, active: str, **extra):
    return TEMPLATES.TemplateResponse(request, name, _ctx(request, active, **extra))


def _ctx(request: Request, active: str, **extra):
    settings = get_settings()
    session = request.state.session
    regime = session.scalar(select(RegimeState).order_by(RegimeState.as_of.desc()))
    paper = session.scalar(select(AccountState).where(AccountState.venue == "paper"))
    live = session.scalar(select(AccountState).where(AccountState.venue == "live"))
    pending = session.scalar(select(func.count(DeskEvent.id)).where(DeskEvent.status == "pending")) or 0
    return {
        "request": request,
        "active": active,
        "theme": settings.app.theme,
        "subtitle": settings.app.subtitle,
        "port": settings.app.port,
        "db_path": str(settings.db_path),
        "now": datetime.now(),
        "regime": {
            "label": regime.desk if regime else "Elevated",
            "tone": (regime.desk if regime else "Elevated").lower(),
            "reason": regime.reason if regime else "",
        },
        "paper": paper,
        "live": live,
        "pending_count": pending,
        "modules": settings.modules,
        "notice": request.query_params.get("notice", ""),
        "auto_on": bool(paper.paper_auto) if paper else False,
        "auto_running": is_running(),
        "auto_note": (paper.last_cycle_note if paper else "") or "",
        "auto_at": paper.last_cycle_at if paper else None,
        "auto_error": last_error(),
        **extra,
    }


@router.get("/", response_class=HTMLResponse)
def command(request: Request):
    session = request.state.session
    paper = session.scalar(select(AccountState).where(AccountState.venue == "paper"))
    live = session.scalar(select(AccountState).where(AccountState.venue == "live"))
    paper_dd = assess_drawdown(paper.equity, paper.peak) if paper else None
    live_dd = assess_drawdown(live.equity, live.peak) if live else None
    signals = list(session.scalars(select(SignalRow).order_by(SignalRow.created_at.desc()).limit(12)))
    positions = decorate_positions(
        session, list(session.scalars(select(Position).where(Position.status == "open")))
    )
    return _render(
        request, "command.html", "command",
        paper_dd=paper_dd, live_dd=live_dd, signals=signals, positions=positions,
    )


def _group_signals_by_symbol(session, window: int = 500) -> list[dict]:
    """2.C.14 — group recent decisions by symbol with a running tally, so a
    pattern like "INFY.F opened and closed 43 times today" (Finding F16,
    re-entry churn) is visible at a glance instead of being 43 separate rows
    a user has to notice by eye.

    Scoped to the most recent `window` signals (a bounded, PK-ordered SQL
    query) rather than the whole table, to keep this cheap while still
    covering "today" in practice for a desk that ticks roughly once a
    minute. Aggregated in Python rather than SQL `GROUP BY` because the
    "most common side" tally needs a nested count-per-(symbol, side) that
    is fiddlier to express as a single aggregate query than to just fold
    over up to `window` already-fetched rows.
    """
    recent = list(session.scalars(select(SignalRow).order_by(SignalRow.id.desc()).limit(window)))
    groups: dict[str, dict] = {}
    for row in recent:
        g = groups.setdefault(
            row.symbol,
            {"symbol": row.symbol, "count": 0, "paper": 0, "live": 0, "sides": {}, "last_at": row.created_at},
        )
        g["count"] += 1
        if row.paper:
            g["paper"] += 1
        if row.live:
            g["live"] += 1
        g["sides"][row.side] = g["sides"].get(row.side, 0) + 1
        if row.created_at > g["last_at"]:
            g["last_at"] = row.created_at
    out = []
    for g in groups.values():
        top_side = max(g["sides"].items(), key=lambda kv: kv[1])[0] if g["sides"] else "—"
        out.append(
            {
                "symbol": g["symbol"],
                "count": g["count"],
                "paper": g["paper"],
                "held": g["count"] - g["paper"],
                "live": g["live"],
                "top_side": top_side,
                "last_at": g["last_at"],
            }
        )
    out.sort(key=lambda x: x["count"], reverse=True)
    return out


@router.get("/signals", response_class=HTMLResponse)
def signals_page(request: Request, before: int | None = None):
    session = request.state.session
    # 2.C.11 — cursor-paginated: `?before=<id>` walks further back in time.
    # `id` is monotonic with `created_at` (both assigned at insert time), so
    # ordering/paging on the integer PK is equivalent to paging on the
    # timestamp but avoids a second index/column comparison.
    query = select(SignalRow).order_by(SignalRow.id.desc())
    if before is not None:
        query = query.where(SignalRow.id < before)
    page = list(session.scalars(query.limit(51)))
    has_more = len(page) > 50
    rows = page[:50]
    next_cursor = rows[-1].id if has_more and rows else None
    events = list(session.scalars(select(DeskEvent).order_by(DeskEvent.created_at.desc()).limit(20)))
    grouped = _group_signals_by_symbol(session)
    return _render(
        request, "signals.html", "signals",
        rows=rows, events=events, next_cursor=next_cursor, grouped=grouped,
    )


def _account_broker(session) -> str:
    paper = session.scalar(select(AccountState).where(AccountState.venue == "paper"))
    if paper and getattr(paper, "broker", None):
        return normalize_broker(paper.broker)
    return normalize_broker(get_settings().account.broker)


@router.get("/book", response_class=HTMLResponse)
def book(request: Request, before: int | None = None):
    session = request.state.session
    broker = _account_broker(session)
    ensure_fill_charges(session, broker)
    repair_shifted_clips(session)
    session.flush()
    paper = decorate_positions(
        session, list(session.scalars(select(Position).where(Position.venue == "paper").order_by(Position.id.desc())))
    )
    live = decorate_positions(
        session, list(session.scalars(select(Position).where(Position.venue == "live").order_by(Position.id.desc())))
    )
    paper_open = [p for p in paper if p["status"] == "open"]
    paper_done = [p for p in paper if p["status"] == "closed"]
    # 2.A.3 — a repaired closed clip with no matching exit fill is flagged
    # `status="unreconciled"` (storage/repair.py) instead of being rewritten
    # against today's mark. That status matches neither `open` nor `closed`
    # above, so without this the row simply vanished from the Book page.
    # Surface it in its own table instead — its exit_price/realized_pnl are
    # None/unreliable, so it stays out of the normal settled P&L columns.
    paper_unreconciled = [p for p in paper if p["status"] == "unreconciled"]
    live_open = [p for p in live if p["status"] == "open"]
    live_done = [p for p in live if p["status"] == "closed"]
    # 2.C.11 — this used to be `select(Fill).order_by(...)` with no `.limit`
    # at all: the *entire* fills table was loaded into memory and only then
    # sliced to `[:40]` in Python. Bound the query in SQL instead, with a
    # `?before=<id>` cursor for "Load more" (id is monotonic with
    # filled_at, so paging on the integer PK is equivalent and cheaper).
    fills_query = select(Fill).order_by(Fill.id.desc())
    if before is not None:
        fills_query = fills_query.where(Fill.id < before)
    fills_page = list(session.scalars(fills_query.limit(41)))
    has_more_fills = len(fills_page) > 40
    fills_page = fills_page[:40]
    next_fills_cursor = fills_page[-1].id if has_more_fills and fills_page else None
    # The "Charges paid" summary is a lifetime total across every fill ever
    # written, not just this page, so it deliberately keeps its own
    # full-history query rather than reusing the paginated slice above.
    all_fills_for_charges = list(session.scalars(select(Fill)))
    return _render(
        request,
        "book.html",
        "book",
        paper_pos=paper_open,
        paper_done=paper_done,
        paper_unreconciled=paper_unreconciled,
        paper_settled=summarize_closed(paper_done),
        live_pos=live_open,
        live_done=live_done,
        live_settled=summarize_closed(live_done),
        fills=decorate_fills(fills_page),
        next_fills_cursor=next_fills_cursor,
        charges=summarize_charges(all_fills_for_charges),
        broker=broker,
        broker_label=broker_label(broker),
    )


def _active_watch_symbols(session) -> list[str]:
    """2.C.9 — the symbol-picker datalist on /chart and /review is sourced
    from the *active* watchlist, matching the filter `pipeline.run_cycle`
    itself uses (`WatchItem.status == "active"`) rather than every symbol
    ever added. Rendered server-side into a `<datalist>` so the picker works
    with zero extra round-trips, matching this app's "server renders, JS
    enhances" style elsewhere (no new API endpoint needed for this).
    """
    return list(
        session.scalars(
            select(WatchItem.symbol).where(WatchItem.status == "active").order_by(WatchItem.symbol)
        )
    )


@router.get("/review", response_class=HTMLResponse)
def review_page(request: Request, symbol: str = "NIFTY"):
    session = request.state.session
    return _render(
        request, "review.html", "review",
        symbol=symbol.upper(), active_symbols=_active_watch_symbols(session),
    )


@router.get("/chart", response_class=HTMLResponse)
def chart_page(request: Request, symbol: str = "RELIANCE"):
    session = request.state.session
    return _render(
        request, "chart.html", "chart",
        symbol=symbol.upper(), active_symbols=_active_watch_symbols(session),
    )


@router.get("/import", response_class=HTMLResponse)
def import_page(request: Request):
    session = request.state.session
    holdings = list(session.scalars(select(Holding).order_by(Holding.created_at.desc()).limit(40)))
    return _render(request, "import.html", "import", holdings=holdings, preview=None)


@router.post("/import", response_class=HTMLResponse)
async def import_post(
    request: Request,
    file: UploadFile = File(...),
    account_name: str = Form("Imported"),
    commit: str = Form(""),
):
    settings = get_settings()
    dest = settings.import_dir
    dest.mkdir(parents=True, exist_ok=True)
    path = dest / (file.filename or "upload.bin")
    path.write_bytes(await file.read())
    session = request.state.session
    svc = ImportService(session)
    preview = svc.preview(path)
    if commit == "1":
        svc.commit(preview, account_name=account_name, source_path=path)
        session.commit()
        return RedirectResponse("/import", status_code=303)
    holdings = list(session.scalars(select(Holding).order_by(Holding.created_at.desc()).limit(40)))
    return _render(
        request, "import.html", "import", holdings=holdings, preview=preview, account_name=account_name
    )


@router.get("/holdings", response_class=HTMLResponse)
def holdings(request: Request):
    session = request.state.session
    rows = list(session.scalars(select(Holding)))
    return _render(request, "holdings.html", "holdings", rows=rows)


@router.get("/watch", response_class=HTMLResponse)
def watch(request: Request):
    session = request.state.session
    rows = list(session.scalars(select(WatchItem).order_by(WatchItem.symbol)))
    return _render(request, "watch.html", "watch", rows=rows)


@router.get("/safety", response_class=HTMLResponse)
def safety_page(request: Request):
    session = request.state.session
    live = session.scalar(select(AccountState).where(AccountState.venue == "live"))
    paper = session.scalar(select(AccountState).where(AccountState.venue == "paper"))
    live_dd = assess_drawdown(live.equity, live.peak) if live else None
    paper_dd = assess_drawdown(paper.equity, paper.peak) if paper else None
    broker = _account_broker(session)
    # 2.A.2 — "Arm live" and "is a broker actually plugged in" used to be two
    # facts the user couldn't see together. Compute the adapter fact here,
    # server-side, from the same registry the OMS itself consults
    # (execution/brokers/plugin.py:get_live_broker) — never guessed in the
    # template or in JS.
    live_broker = get_live_broker()
    return _render(
        request,
        "safety.html",
        "safety",
        live_dd=live_dd,
        paper_dd=paper_dd,
        broker=broker,
        broker_label=broker_label(broker),
        brokers=[{"id": name, "label": BROKER_LABELS[name]} for name in BROKERS],
        live_broker_name=live_broker.name if live_broker else None,
    )


@router.get("/help", response_class=HTMLResponse)
def help_page(request: Request):
    return _render(request, "help.html", "help")


@router.post("/desk/cycle")
def desk_cycle(request: Request):
    with desk_lock:
        result = run_cycle(request.state.session)
        request.state.session.commit()
    extra = ""
    if result["paper_opened"] == 0:
        extra = (
            f" No new clip: {result.get('open_count', 0)} already open, "
            f"cash ₹{result.get('cash', 0):,.0f}. "
            "Already-held names are not bought again."
        )
    # 2.7 — this used to hardcode "Live stays disarmed." regardless of the
    # real live_armed state (F13). Read it from what run_cycle actually
    # returned instead of asserting a fixed string.
    live_line = "Live is armed — clips can go live." if result.get("live_armed") else "Live stays disarmed."
    notice = (
        f"Cycle finished. Paper fills: {result['paper_opened']}. "
        f"Hold: {result.get('holds', 0)}. {live_line}{extra}"
    )
    return RedirectResponse("/?notice=" + _q(notice), status_code=303)


@router.post("/desk/seed")
def desk_seed(request: Request):
    with desk_lock:
        added = seed_demo(request.state.session)
        from meridian_v3.data_providers.service import PriceProvider

        PriceProvider(request.state.session).refresh_alt_markets()
        set_paper_auto(request.state.session, True)
        result = tick(request.state.session, refresh_prices=False)
        request.state.session.commit()
    head = (
        f"Demo desk refreshed. Added {added} missing piece(s)."
        if added
        else "Demo desk was already in place."
    )
    notice = (
        f"{head} Paper auto is ON. It will keep choosing and paper-trading "
        f"while this window is open. This pass: {result.get('note', '')}"
    )
    return RedirectResponse("/?notice=" + _q(notice), status_code=303)


@router.post("/desk/auto")
def desk_auto(request: Request, on: str = Form("1")):
    with desk_lock:
        want = on == "1"
        set_paper_auto(request.state.session, want)
        if want:
            tick(request.state.session, refresh_prices=False)
            notice = "Paper auto is ON. The demo account will keep trading on its own."
        else:
            notice = "Paper auto is OFF. Nothing new will be paper-traded until you start it again."
        request.state.session.commit()
    return RedirectResponse("/?notice=" + _q(notice), status_code=303)


@router.post("/desk/arm")
def desk_arm(request: Request, on: str = Form("0")):
    session = request.state.session
    live = session.scalar(select(AccountState).where(AccountState.venue == "live"))
    if live:
        live.live_armed = 1 if on == "1" else 0
        live.updated_at = datetime.now(timezone.utc).replace(tzinfo=None)
        session.commit()
        # 2.6 — arming live moves real money once a broker is registered;
        # a toggle this consequential belongs in the log, not just the DB.
        logger.info("live arm toggled: live_armed={}", bool(live.live_armed))
    return RedirectResponse("/safety", status_code=303)


@router.post("/desk/broker")
def desk_broker(request: Request, broker: str = Form("zerodha")):
    session = request.state.session
    name = normalize_broker(broker)
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    for venue in ("paper", "live"):
        acct = session.scalar(select(AccountState).where(AccountState.venue == venue))
        if acct:
            acct.broker = name
            acct.updated_at = now
    session.commit()
    notice = (
        f"Fee sheet is now {broker_label(name)}. "
        "New fills use that broker's brokerage plus GST, STT, stamp and exchange."
    )
    return RedirectResponse("/safety?notice=" + _q(notice), status_code=303)
