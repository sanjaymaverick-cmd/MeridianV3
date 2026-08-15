"""Part 3 item 6 — alerting for state changes that matter.

Covers ``meridian_v3.alerts.notify.emit_alert`` itself (log line, DeskEvent
row, optional webhook that never crashes the caller) and all four trigger
points it was wired into: the drawdown-pause edge-detection, the live
arm/disarm toggle, the autopilot worker's own failure, and a large repair
run.
"""

from __future__ import annotations

from datetime import datetime, timezone

import httpx
import pytest
from sqlalchemy import select

from meridian_v3.alerts.notify import emit_alert
from meridian_v3.engine.drawdown import DrawdownState
from meridian_v3.storage.schema import AccountState, DeskEvent


def _now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


# ---------------------------------------------------------------------------
# emit_alert() itself
# ---------------------------------------------------------------------------


def test_emit_alert_writes_a_desk_event_row(session):
    emit_alert(session, "test_kind", "something happened")
    session.flush()
    row = session.scalar(select(DeskEvent).where(DeskEvent.rule_name == "test_kind"))
    assert row is not None
    assert row.policy_kind == "alert"
    assert row.reason == "something happened"
    assert row.status == "pending"


def test_emit_alert_with_no_webhook_configured_never_calls_httpx(session, monkeypatch):
    from meridian_v3.alerts import notify as notify_module

    settings = notify_module.get_settings()
    assert settings.alerts.webhook_url is None  # default: opt-in, unset

    called = []
    monkeypatch.setattr(httpx, "post", lambda *a, **k: called.append((a, k)))
    emit_alert(session, "no_webhook", "no network should happen")
    assert called == []


def test_emit_alert_posts_to_webhook_when_configured(session, monkeypatch):
    from meridian_v3.alerts import notify as notify_module

    settings = notify_module.get_settings()
    settings.alerts.webhook_url = "https://example.invalid/hook"

    calls = []

    def _fake_post(url, json=None, timeout=None):
        calls.append((url, json, timeout))

        class _Resp:
            status_code = 200

        return _Resp()

    monkeypatch.setattr(notify_module.httpx, "post", _fake_post)
    emit_alert(session, "webhook_kind", "webhook message")
    assert len(calls) == 1
    url, payload, timeout = calls[0]
    assert url == "https://example.invalid/hook"
    assert payload["kind"] == "webhook_kind"
    assert payload["message"] == "webhook message"
    assert "at" in payload
    assert timeout == notify_module._WEBHOOK_TIMEOUT_SECONDS


def test_emit_alert_webhook_failure_does_not_raise(session, monkeypatch):
    from meridian_v3.alerts import notify as notify_module

    settings = notify_module.get_settings()
    settings.alerts.webhook_url = "https://example.invalid/hook"

    def _boom(*a, **k):
        raise httpx.ConnectTimeout("nope")

    monkeypatch.setattr(notify_module.httpx, "post", _boom)
    # Must not raise — a dead webhook must never crash the caller.
    emit_alert(session, "webhook_down", "should not raise")
    row = session.scalar(select(DeskEvent).where(DeskEvent.rule_name == "webhook_down"))
    assert row is not None  # the DeskEvent/log side still happened


# ---------------------------------------------------------------------------
# Drawdown-pause edge detection — fires once per pause episode, not a flood
# ---------------------------------------------------------------------------


def _paused(reason="paused"):
    return DrawdownState(equity=39_000, peak=50_000, drawdown=0.22, scale=0.0, live_paused=True, reason=reason)


def _recovered():
    return DrawdownState(equity=50_000, peak=50_000, drawdown=0.0, scale=1.0, live_paused=False, reason="fine")


def test_drawdown_pause_alerts_once_then_stays_quiet_while_still_paused(session, monkeypatch):
    from meridian_v3 import pipeline

    calls = []
    monkeypatch.setattr(pipeline, "emit_alert", lambda s, kind, msg: calls.append((kind, msg)))

    acct = AccountState(venue="paper", cash=39_000, equity=39_000, peak=50_000, updated_at=_now())
    session.add(acct)
    session.flush()

    pipeline.alert_on_drawdown_transition(session, acct, _paused())
    assert len(calls) == 1
    assert acct.live_pause_alerted == 1

    # Still paused next cycle — must not alert again.
    pipeline.alert_on_drawdown_transition(session, acct, _paused())
    pipeline.alert_on_drawdown_transition(session, acct, _paused())
    assert len(calls) == 1


def test_drawdown_pause_alerts_again_after_recovering_and_re_pausing(session, monkeypatch):
    from meridian_v3 import pipeline

    calls = []
    monkeypatch.setattr(pipeline, "emit_alert", lambda s, kind, msg: calls.append((kind, msg)))

    acct = AccountState(venue="paper", cash=39_000, equity=39_000, peak=50_000, updated_at=_now())
    session.add(acct)
    session.flush()

    pipeline.alert_on_drawdown_transition(session, acct, _paused())
    assert len(calls) == 1

    # Recovers — the latch resets, no new alert on the recovery itself.
    pipeline.alert_on_drawdown_transition(session, acct, _recovered())
    assert len(calls) == 1
    assert acct.live_pause_alerted == 0

    # Pauses again — a second, distinct episode must alert again.
    pipeline.alert_on_drawdown_transition(session, acct, _paused())
    assert len(calls) == 2


# ---------------------------------------------------------------------------
# Live arm/disarm toggle
# ---------------------------------------------------------------------------


def test_desk_arm_route_fires_an_alert(session):
    from fastapi.testclient import TestClient

    from meridian_v3.app import create_app
    from meridian_v3.storage.seed import seed_demo

    seed_demo(session, reset=True)
    session.commit()
    client = TestClient(create_app())

    res = client.post("/desk/arm", data={"on": "1"}, follow_redirects=False)
    assert res.status_code == 303

    row = session.scalar(select(DeskEvent).where(DeskEvent.rule_name == "live_arm_toggled"))
    assert row is not None
    assert "ARMED" in row.reason


def test_cli_arm_fires_an_alert(session):
    import meridian_v3.cli as cli_module
    from meridian_v3.storage.seed import seed_demo

    seed_demo(session, reset=True)
    session.commit()

    rc = cli_module.main(["arm", "--on"])
    assert rc == 0
    row = session.scalar(select(DeskEvent).where(DeskEvent.rule_name == "live_arm_toggled"))
    assert row is not None
    assert "ARMED" in row.reason


# ---------------------------------------------------------------------------
# Autopilot worker death
# ---------------------------------------------------------------------------


def test_autopilot_loop_emits_worker_error_alert(session, monkeypatch):
    """Exercises the real ``_loop()`` (not a reimplementation of its body).

    A fake ``_stop`` event lets the loop run exactly one iteration without
    any real sleeping, and a session whose ``.scalar()`` always raises drives
    it down the ``except Exception`` path — the same path a real crash (bad
    data, a DB hiccup, whatever) would take.
    """
    import meridian_v3.autopilot as autopilot_module

    alerts = []
    monkeypatch.setattr(autopilot_module, "emit_alert", lambda s, kind, msg: alerts.append((kind, msg)))

    class _FakeEvent:
        def __init__(self):
            self._checks = 0

        def wait(self, timeout=None):
            return False

        def is_set(self):
            self._checks += 1
            return self._checks > 1  # False the first check (enter loop), True after (exit)

    class _BoomSession:
        def scalar(self, *a, **k):
            raise RuntimeError("boom")

        def rollback(self):
            pass

        def commit(self):
            pass

        def close(self):
            pass

    monkeypatch.setattr(autopilot_module, "_stop", _FakeEvent())
    monkeypatch.setattr(autopilot_module, "get_session", lambda: _BoomSession())

    autopilot_module._loop()

    assert len(alerts) == 1
    assert alerts[0][0] == "worker_error"
    assert "boom" in alerts[0][1]


def test_autopilot_loop_alert_failure_does_not_crash_the_worker(session, monkeypatch):
    """The alert call itself is wrapped in its own try/except (autopilot.py's
    whole point is staying alive) — a broken emit_alert must not propagate."""
    import meridian_v3.autopilot as autopilot_module

    def _boom_alert(*a, **k):
        raise RuntimeError("alerting is broken too")

    monkeypatch.setattr(autopilot_module, "emit_alert", _boom_alert)

    class _FakeEvent:
        def __init__(self):
            self._checks = 0

        def wait(self, timeout=None):
            return False

        def is_set(self):
            self._checks += 1
            return self._checks > 1

    class _BoomSession:
        def scalar(self, *a, **k):
            raise RuntimeError("boom")

        def rollback(self):
            pass

        def commit(self):
            pass

        def close(self):
            pass

    monkeypatch.setattr(autopilot_module, "_stop", _FakeEvent())
    monkeypatch.setattr(autopilot_module, "get_session", lambda: _BoomSession())

    # Must not raise, despite both the tick and the alert failing.
    autopilot_module._loop()


# ---------------------------------------------------------------------------
# Large repair run
# ---------------------------------------------------------------------------


def test_repair_alert_fires_above_threshold_not_below(session, monkeypatch):
    import meridian_v3.storage.repair as repair_module

    calls = []
    monkeypatch.setattr(repair_module, "emit_alert", lambda s, kind, msg: calls.append((kind, msg)))

    monkeypatch.setattr(repair_module, "repair_shifted_clips", lambda s: 3)
    assert repair_module.repair_margin_priced_clips(session) == 3
    assert calls == []

    monkeypatch.setattr(repair_module, "repair_shifted_clips", lambda s: 4)
    assert repair_module.repair_margin_priced_clips(session) == 4
    assert len(calls) == 1
    assert calls[0][0] == "large_repair_run"
    assert "4" in calls[0][1]
