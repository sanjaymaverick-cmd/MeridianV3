"""Outbound alerting for state changes that matter (Part 3 item 6).

Nothing used to notify the operator when drawdown crossed the pause
threshold, live got armed/disarmed, the autopilot worker died, or a repair
run changed more than a few rows — the only record was whatever happened to
land in a ``DeskEvent`` row on the next page load. This closes that gap with
zero required config:

  * every alert is logged via loguru at WARNING level (visible in the log
    file ``logging.py:setup_logging()`` already wires up), and
  * every alert is written as a ``DeskEvent`` row, so it shows up in-app on
    ``signals.html``'s "Pending reviews" feed without any new UI surface.

A webhook POST is an *optional* extra: it only fires when
``settings.alerts.webhook_url`` is configured (unset by default). A network
failure, timeout, or bad URL there must never crash the desk or the cycle
that triggered the alert — it is always caught and logged, never raised.
"""

from __future__ import annotations

from datetime import datetime, timezone

import httpx
from loguru import logger
from sqlalchemy.orm import Session

from meridian_v3.config import get_settings
from meridian_v3.storage.schema import DeskEvent

# Short timeout: an alert must never make the caller (a cycle, the
# autopilot worker, a request handler) hang waiting on a slow/dead webhook.
_WEBHOOK_TIMEOUT_SECONDS = 5.0


def emit_alert(session: Session, kind: str, message: str) -> None:
    """Log, record, and (optionally) push a notification for an operator-relevant event.

    ``kind`` is a short machine-readable tag (e.g. ``"drawdown_pause"``,
    ``"live_arm_toggled"``, ``"worker_error"``, ``"large_repair_run"``).
    ``message`` is the plain-language reason, following this codebase's
    existing reason-string style.

    Always does two things:
      1. ``logger.warning`` — one line, always visible in the log file.
      2. Adds a ``DeskEvent(policy_kind="alert", rule_name=kind, reason=message)``
         row to ``session``. The caller is responsible for committing, same as
         every other write in this codebase — this function never commits.

    Optionally POSTs ``{"kind", "message", "at"}`` to
    ``settings.alerts.webhook_url`` when one is configured. Unset (the
    default) means this branch never runs at all — no network call is
    attempted — so alerting works out of the box with zero external config.
    """
    logger.warning("ALERT [{}]: {}", kind, message)
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    session.add(
        DeskEvent(
            policy_kind="alert",
            symbol="",
            rule_name=kind,
            reason=message,
            copy_review=message,
            payload_json="{}",
            status="pending",
            created_at=now,
        )
    )

    settings = get_settings()
    webhook_url = settings.alerts.webhook_url
    if not webhook_url:
        return
    try:
        httpx.post(
            webhook_url,
            json={"kind": kind, "message": message, "at": now.isoformat() + "Z"},
            timeout=_WEBHOOK_TIMEOUT_SECONDS,
        )
    except Exception as exc:  # noqa: BLE001 — a failed webhook must never crash the desk
        logger.warning("alert webhook POST failed ({}): {}", kind, exc)
