"""2.6 — `setup_logging()` was fully written (loguru, rotation, retention)
but never called from anywhere. Confirm it now runs at both real entry
points: the FastAPI app and the CLI.
"""

from __future__ import annotations


def test_setup_logging_is_called_at_app_startup(session, monkeypatch):
    import meridian_v3.app as app_module

    calls = []
    monkeypatch.setattr(app_module, "setup_logging", lambda: calls.append("app"))
    app_module.create_app()
    assert calls == ["app"]


def test_setup_logging_is_called_at_cli_startup(session, monkeypatch):
    import meridian_v3.cli as cli_module

    calls = []
    monkeypatch.setattr(cli_module, "setup_logging", lambda: calls.append("cli"))
    rc = cli_module.main(["seed"])
    assert rc == 0
    assert "cli" in calls


def test_setup_logging_actually_creates_the_log_file(tmp_path, monkeypatch):
    """Positive control on `logging.py` itself: a real call writes the file."""
    from meridian_v3.config import Settings
    from meridian_v3 import logging as logging_module

    settings = Settings()
    settings.paths.log_dir = str(tmp_path)
    monkeypatch.setattr(logging_module, "get_settings", lambda: settings)

    logging_module.setup_logging()
    assert (tmp_path / "meridian_v3.log").exists()
