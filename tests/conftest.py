from __future__ import annotations

import os
from pathlib import Path

import pytest

os.environ.setdefault("MERIDIAN_V3_TEST_DB", str(Path(__file__).resolve().parent / "_tmp_test.db"))


@pytest.fixture()
def settings(tmp_path, monkeypatch):
    from meridian_v3.config import reset_settings_cache
    from meridian_v3.storage.db import reset_engine

    db = tmp_path / "meridian_v3_test.db"
    monkeypatch.setenv("MERIDIAN_V3_TEST_DB", str(db))
    reset_settings_cache()
    reset_engine()
    from meridian_v3.config import get_settings
    from meridian_v3.storage.db import init_db

    s = get_settings()
    # force the test db path even if env prefix didn't bind
    object.__setattr__(s, "test_db", str(db)) if False else None
    s.test_db = str(db)
    init_db()
    yield s
    reset_engine()
    reset_settings_cache()


@pytest.fixture()
def session(settings):
    from meridian_v3.storage.db import get_session

    sess = get_session()
    try:
        yield sess
        sess.commit()
    finally:
        sess.close()
