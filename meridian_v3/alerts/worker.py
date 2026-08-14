from __future__ import annotations

import time

from meridian_v3.config import get_settings
from meridian_v3.pipeline import run_cycle
from meridian_v3.storage.db import get_session


def run_once() -> dict:
    session = get_session()
    try:
        result = run_cycle(session)
        session.commit()
        return result
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def run_worker() -> None:
    settings = get_settings()
    while True:
        run_once()
        time.sleep(settings.alerts.poll_seconds)
