from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote

from fastapi import APIRouter, File, Form, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, select

from meridian_v3.config import get_settings
from meridian_v3.domain.money import format_inr, format_pct
from meridian_v3.engine.drawdown import assess_drawdown
from meridian_v3.ingestion.service import ImportService
from meridian_v3.pipeline import run_cycle
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
    positions = list(session.scalars(select(Position).where(Position.status == "open")))
    return _render(
        request, "command.html", "command",
        paper_dd=paper_dd, live_dd=live_dd, signals=signals, positions=positions,
    )


@router.get("/signals", response_class=HTMLResponse)
def signals_page(request: Request):
    session = request.state.session
    rows = list(session.scalars(select(SignalRow).order_by(SignalRow.created_at.desc()).limit(50)))
    events = list(session.scalars(select(DeskEvent).order_by(DeskEvent.created_at.desc()).limit(20)))
    return _render(request, "signals.html", "signals", rows=rows, events=events)


@router.get("/book", response_class=HTMLResponse)
def book(request: Request):
    session = request.state.session
    paper = list(session.scalars(select(Position).where(Position.venue == "paper")))
    live = list(session.scalars(select(Position).where(Position.venue == "live")))
    fills = list(session.scalars(select(Fill).order_by(Fill.filled_at.desc()).limit(30)))
    return _render(request, "book.html", "book", paper_pos=paper, live_pos=live, fills=fills)


@router.get("/review", response_class=HTMLResponse)
def review_page(request: Request):
    return _render(request, "review.html", "review", symbol="NIFTY")


@router.get("/chart", response_class=HTMLResponse)
def chart_page(request: Request, symbol: str = "RELIANCE"):
    return _render(request, "chart.html", "chart", symbol=symbol.upper())


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
    return _render(request, "safety.html", "safety", live_dd=live_dd, paper_dd=paper_dd)


@router.get("/help", response_class=HTMLResponse)
def help_page(request: Request):
    return _render(request, "help.html", "help")


@router.post("/desk/cycle")
def desk_cycle(request: Request):
    result = run_cycle(request.state.session)
    request.state.session.commit()
    notice = (
        f"Cycle finished. Paper fills: {result['paper_opened']}. "
        f"Hold: {result.get('holds', 0)}. Live stays disarmed."
    )
    return RedirectResponse("/?notice=" + _q(notice), status_code=303)


@router.post("/desk/seed")
def desk_seed(request: Request):
    added = seed_demo(request.state.session)
    result = run_cycle(request.state.session)
    request.state.session.commit()
    if added:
        head = f"Demo desk refreshed. Added {added} missing piece(s)."
    else:
        head = "Demo desk was already in place. Ran a fresh cycle."
    notice = (
        f"{head} Paper fills this pass: {result['paper_opened']}. "
        "Open Paper / Live to see the tickets."
    )
    return RedirectResponse("/?notice=" + _q(notice), status_code=303)


@router.post("/desk/arm")
def desk_arm(request: Request, on: str = Form("0")):
    session = request.state.session
    live = session.scalar(select(AccountState).where(AccountState.venue == "live"))
    if live:
        live.live_armed = 1 if on == "1" else 0
        live.updated_at = datetime.now(timezone.utc).replace(tzinfo=None)
        session.commit()
    return RedirectResponse("/safety", status_code=303)
