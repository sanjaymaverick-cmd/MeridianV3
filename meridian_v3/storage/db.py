from __future__ import annotations

import threading
from datetime import datetime, timezone

from loguru import logger
from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import Session, sessionmaker

from meridian_v3.config import get_settings
from meridian_v3.storage.schema import Base

_engine = None
_Session: sessionmaker | None = None
desk_lock = threading.RLock()


def get_engine():
    global _engine, _Session
    if _engine is None:
        settings = get_settings()
        settings.ensure_dirs()
        _engine = create_engine(
            f"sqlite:///{settings.db_path}",
            future=True,
            connect_args={
                "check_same_thread": False,
                "timeout": 30,
            },
        )

        @event.listens_for(_engine, "connect")
        def _sqlite_pragmas(dbapi_conn, _connection_record) -> None:  # noqa: ANN001
            cursor = dbapi_conn.cursor()
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute("PRAGMA busy_timeout=30000")
            cursor.execute("PRAGMA synchronous=NORMAL")
            cursor.close()

        _Session = sessionmaker(bind=_engine, future=True, expire_on_commit=False)
    return _engine


def init_db() -> None:
    engine = get_engine()
    Base.metadata.create_all(engine)
    _migrate(engine)
    _repair_book()


def _migrate(engine) -> None:
    """Add columns that create_all will not attach to an older SQLite file."""
    wanted = {
        "paper_auto": "INTEGER DEFAULT 1",
        "last_cycle_at": "DATETIME",
        "last_cycle_note": "TEXT DEFAULT ''",
        "broker": "VARCHAR(24) DEFAULT 'zerodha'",
        # Part 3 item 6 — drawdown-pause alert edge-detection flag.
        "live_pause_alerted": "INTEGER DEFAULT 0",
    }
    with engine.begin() as conn:
        rows = conn.execute(text("PRAGMA table_info(account_state)")).fetchall()
        have = {row[1] for row in rows}
        for name, ddl in wanted.items():
            if name not in have:
                conn.execute(text(f"ALTER TABLE account_state ADD COLUMN {name} {ddl}"))

        # 2.3 — old paper books started at ₹5,000; credit the gap so demo
        # cash is ₹50,000. Gated behind an explicit schema_version marker
        # instead of a `peak <= 5000.01` heuristic: that heuristic skipped a
        # partially-compounded old-scale book (peak already above 5,000) and
        # would double-credit a book whose peak later drew back down near
        # zero. `schema_version` makes "already migrated" an explicit fact,
        # not something re-derived from the numbers every boot.
        _run_once(
            conn,
            "credit_5000_to_50000",
            """
            UPDATE account_state
            SET cash = cash + (50000 - peak),
                equity = equity + (50000 - peak),
                peak = 50000
            WHERE peak < 50000 AND peak > 0
            """,
        )

        pos_cols = {row[1] for row in conn.execute(text("PRAGMA table_info(positions)")).fetchall()}
        for name, ddl in {
            "exit_price": "FLOAT",
            "realized_pnl": "FLOAT",
            "close_qty": "FLOAT",
            "feature_json": "TEXT DEFAULT '{}'",
            "opened_confidence": "FLOAT DEFAULT 0.0",
        }.items():
            if name not in pos_cols:
                conn.execute(text(f"ALTER TABLE positions ADD COLUMN {name} {ddl}"))
        fill_cols = {row[1] for row in conn.execute(text("PRAGMA table_info(fills)")).fetchall()}
        if "charges_json" not in fill_cols:
            conn.execute(text("ALTER TABLE fills ADD COLUMN charges_json TEXT DEFAULT '{}'"))
        # 2.4 — corrections from a later repair live here, never mutated
        # into the original `note` text.
        if "correction_note" not in fill_cols:
            conn.execute(text("ALTER TABLE fills ADD COLUMN correction_note TEXT DEFAULT ''"))


def _run_once(conn, migration: str, sql: str) -> None:
    """Run ``sql`` only if ``migration`` has never been recorded (2.3).

    Idempotent by construction: a fresh DB has no rows to touch and records
    the marker anyway (nothing to credit twice, later); an already-migrated
    DB is a no-op because the marker short-circuits before ``sql`` runs.
    """
    already = conn.execute(
        text("SELECT 1 FROM schema_version WHERE migration = :m"), {"m": migration}
    ).fetchone()
    if already is not None:
        return
    conn.execute(text(sql))
    conn.execute(
        text("INSERT INTO schema_version (migration, migrated_at) VALUES (:m, :t)"),
        {"m": migration, "t": datetime.now(timezone.utc).replace(tzinfo=None)},
    )


def _repair_book() -> None:
    if _Session is None:
        return
    session = _Session()
    try:
        from meridian_v3.storage.repair import repair_margin_priced_clips

        if repair_margin_priced_clips(session):
            session.commit()
        else:
            session.rollback()
    except Exception:
        # 2.6 — a boot repair failure used to vanish with zero diagnostic
        # output. Log it before rolling back so a bad boot leaves a trail.
        logger.exception("boot repair failed")
        session.rollback()
    finally:
        session.close()


def get_session() -> Session:
    if _Session is None:
        get_engine()
    assert _Session is not None
    return _Session()


def reset_engine() -> None:
    global _engine, _Session
    if _engine is not None:
        _engine.dispose()
    _engine = None
    _Session = None
