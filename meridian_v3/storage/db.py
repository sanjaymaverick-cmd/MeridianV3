from __future__ import annotations

import threading

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
    }
    with engine.begin() as conn:
        rows = conn.execute(text("PRAGMA table_info(account_state)")).fetchall()
        have = {row[1] for row in rows}
        for name, ddl in wanted.items():
            if name not in have:
                conn.execute(text(f"ALTER TABLE account_state ADD COLUMN {name} {ddl}"))
        # Old paper books started at ₹5,000. Credit the gap so demo cash is ₹50,000.
        conn.execute(
            text(
                """
                UPDATE account_state
                SET cash = cash + (50000 - peak),
                    equity = equity + (50000 - peak),
                    peak = 50000
                WHERE peak <= 5000.01 AND peak > 0
                """
            )
        )
        pos_cols = {row[1] for row in conn.execute(text("PRAGMA table_info(positions)")).fetchall()}
        for name, ddl in {
            "exit_price": "FLOAT",
            "realized_pnl": "FLOAT",
            "close_qty": "FLOAT",
            "feature_json": "TEXT DEFAULT '{}'",
        }.items():
            if name not in pos_cols:
                conn.execute(text(f"ALTER TABLE positions ADD COLUMN {name} {ddl}"))
        fill_cols = {row[1] for row in conn.execute(text("PRAGMA table_info(fills)")).fetchall()}
        if "charges_json" not in fill_cols:
            conn.execute(text("ALTER TABLE fills ADD COLUMN charges_json TEXT DEFAULT '{}'"))


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
