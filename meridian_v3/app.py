from __future__ import annotations

import os
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.base import BaseHTTPMiddleware

from loguru import logger

from meridian_v3 import __version__
from meridian_v3.api.routes import router as api_router
from meridian_v3.config import get_settings
from meridian_v3.logging import setup_logging
from meridian_v3.storage.db import get_session, init_db
from meridian_v3.ui.routes import router as ui_router

STATIC_DIR = Path(__file__).resolve().parent / "ui" / "static"


class SessionMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        session = get_session()
        request.state.session = session
        try:
            response = await call_next(request)
            session.commit()
            return response
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()


def create_app() -> FastAPI:
    # 2.6 — the log file that already had a writer (loguru, 5MB rotation,
    # 14-day retention) but nothing ever called. Turn it on before touching
    # the DB so a boot-repair failure in init_db() has somewhere to land.
    setup_logging()
    init_db()
    settings = get_settings()
    logger.info("MERIDIAN V3 starting up (version {}).", __version__)

    # Item 7 (Part 3) — `register-dry-run-broker` alone cannot make the live
    # path testable: it's a separate CLI invocation (a separate OS process)
    # from `serve`, so registering there and exiting never reaches the
    # process that actually runs the desk. This env var is the real opt-in:
    # set it before launching `serve` (same process, same _REGISTRY) so
    # `get_live_broker()` genuinely returns a DryRunBroker for that run.
    # Unset by default — never registered automatically.
    if os.environ.get("MERIDIAN_V3_DRY_RUN_BROKER"):
        from meridian_v3.execution.brokers.dry_run import DryRunBroker
        from meridian_v3.execution.brokers.plugin import register_broker

        register_broker(DryRunBroker())
        logger.warning(
            "MERIDIAN_V3_DRY_RUN_BROKER is set — DryRunBroker registered as the live "
            "broker adapter. This is NOT a real venue; live 'place' calls are logged "
            "and synthetic. Unset this env var for a normal run."
        )
    app = FastAPI(
        title=settings.app.name,
        version=__version__,
        description="MERIDIAN V3 personal auto-trading desk. Isolated from v1 and v2.",
    )
    app.add_middleware(SessionMiddleware)
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
    app.include_router(ui_router)
    app.include_router(api_router, prefix="/api")

    @app.on_event("startup")
    def _start_paper_auto() -> None:
        import os

        from meridian_v3.autopilot import start_autopilot

        if settings.alerts.auto_start and not os.environ.get("MERIDIAN_V3_TEST_DB"):
            start_autopilot()

    @app.get("/favicon.ico", include_in_schema=False)
    def favicon() -> FileResponse:
        ico = STATIC_DIR / "favicon.ico"
        if ico.exists():
            return FileResponse(ico, media_type="image/x-icon")
        return FileResponse(STATIC_DIR / "favicon.svg", media_type="image/svg+xml")

    return app
