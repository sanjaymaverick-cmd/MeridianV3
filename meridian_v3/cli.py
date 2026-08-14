from __future__ import annotations

import argparse
import sys
import threading
import time
import webbrowser

import uvicorn

from meridian_v3.config import get_settings
from meridian_v3.storage.db import get_session, init_db
from meridian_v3.storage.seed import seed_demo


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="meridian_v3",
        description="MERIDIAN V3 personal auto-trading desk (isolated from v1 and v2)",
    )
    sub = parser.add_subparsers(dest="cmd")
    sub.add_parser("serve", help="Start the local V3 desk (default, port 8777)")
    seed = sub.add_parser("seed", help="Load the demonstration ₹5,000 book if empty")
    seed.add_argument("--reset", action="store_true")
    sub.add_parser("cycle", help="Run one signal → paper (maybe live) cycle")
    prices = sub.add_parser("prices", help="Refresh marks from free sources")
    prices.add_argument("--force", action="store_true")
    imp = sub.add_parser("import", help="Preview or commit a portfolio statement")
    imp.add_argument("--file", required=True)
    imp.add_argument("--account", default="Imported")
    imp.add_argument("--commit", action="store_true")
    arm = sub.add_parser("arm", help="Arm or disarm live execution")
    arm.add_argument("--on", action="store_true")
    arm.add_argument("--off", action="store_true")
    sub.add_parser("desktop", help="Open MERIDIAN V3 in a native window")
    args = parser.parse_args(argv)

    init_db()

    if args.cmd == "seed":
        return _seed(reset=args.reset)
    if args.cmd == "cycle":
        return _cycle()
    if args.cmd == "prices":
        return _prices(force=args.force)
    if args.cmd == "import":
        return _import(args.file, args.account, args.commit)
    if args.cmd == "arm":
        return _arm(on=args.on and not args.off)
    if args.cmd == "desktop":
        return _desktop()
    return _serve()


def _seed(*, reset: bool) -> int:
    session = get_session()
    try:
        written = seed_demo(session, reset=reset)
        session.commit()
        print(f"seeded {written} watch names" if written else "desk already has data")
        return 0
    finally:
        session.close()


def _cycle() -> int:
    from meridian_v3.pipeline import run_cycle

    session = get_session()
    try:
        result = run_cycle(session)
        session.commit()
        print(f"cycle decided {result['decided']} paper {result['paper_opened']} live_armed {result['live_armed']}")
        return 0
    finally:
        session.close()


def _prices(*, force: bool) -> int:
    from meridian_v3.data_providers.service import PriceProvider

    session = get_session()
    try:
        result = PriceProvider(session).refresh(force=force)
        session.commit()
        print(f"prices marked {result.get('marked', 0)} failed {result.get('failed', 0)}")
        return 0 if not result.get("failed") else 1
    finally:
        session.close()


def _import(path: str, account: str, commit: bool) -> int:
    from pathlib import Path

    from meridian_v3.ingestion.service import ImportService

    target = Path(path)
    if not target.exists():
        print(f"file not found: {path}", file=sys.stderr)
        return 2
    session = get_session()
    try:
        svc = ImportService(session)
        preview = svc.preview(target)
        print(f"broker {preview.broker} kind {preview.source_kind} accepted {preview.accepted} rejected {preview.rejected}")
        for row in preview.rows:
            mark = "OK" if not row.error else "NO"
            print(f"  {mark}  {row.symbol:12} {row.quantity} @ {row.avg_cost}  {row.error or ''}")
        if commit:
            accepted, rejected = svc.commit(preview, account_name=account, source_path=target)
            session.commit()
            print(f"committed {accepted} rejected {rejected}")
        else:
            print("preview only — pass --commit after you have checked every line")
        return 0
    finally:
        session.close()


def _arm(*, on: bool) -> int:
    from datetime import datetime, timezone

    from sqlalchemy import select

    from meridian_v3.storage.schema import AccountState

    session = get_session()
    try:
        live = session.scalar(select(AccountState).where(AccountState.venue == "live"))
        if live is None:
            print("desk is not seeded", file=sys.stderr)
            return 1
        live.live_armed = 1 if on else 0
        live.updated_at = datetime.now(timezone.utc).replace(tzinfo=None)
        session.commit()
        print("live ARMED" if on else "live DISARMED — paper still runs")
        return 0
    finally:
        session.close()


def _serve(*, open_browser: bool | None = None, log_level: str = "info") -> int:
    settings = get_settings()
    url = f"http://{settings.app.host}:{settings.app.port}"
    should_open = settings.app.open_browser if open_browser is None else open_browser
    if should_open:
        threading.Thread(target=_open, args=(url,), daemon=True).start()
    uvicorn.run(
        "meridian_v3.app:create_app",
        factory=True,
        host=settings.app.host,
        port=settings.app.port,
        reload=False,
        log_level=log_level,
    )
    return 0


def _desktop() -> int:
    settings = get_settings()
    url = f"http://{settings.app.host}:{settings.app.port}"
    try:
        import webview
    except ImportError:
        print("pywebview missing — opening the browser desk. pip install 'meridian-v3[desktop]'", file=sys.stderr)
        return _serve()
    thread = threading.Thread(target=_serve, kwargs={"open_browser": False, "log_level": "warning"}, daemon=True)
    thread.start()
    time.sleep(0.8)
    webview.create_window("MERIDIAN V3", url, width=1440, height=900, min_size=(1100, 700))
    webview.start()
    return 0


def _open(url: str) -> None:
    time.sleep(0.8)
    webbrowser.open(url)


if __name__ == "__main__":
    raise SystemExit(main())
