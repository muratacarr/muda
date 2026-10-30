"""SQLite engine setup: WAL, foreign keys, busy timeout, explicit write transactions."""

from typing import Any

from sqlalchemy import Engine, create_engine, event

from muda.persistence.models import Base


def create_db_engine(url: str) -> Engine:
    engine = create_engine(url, connect_args={"timeout": 15})

    @event.listens_for(engine, "connect")
    def _on_connect(dbapi_conn: Any, _record: Any) -> None:
        # Disable pysqlite's implicit transaction handling; write_tx() issues
        # BEGIN IMMEDIATE explicitly so writers take the lock up front.
        dbapi_conn.isolation_level = None
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA journal_mode=WAL")
        cur.execute("PRAGMA foreign_keys=ON")
        cur.execute("PRAGMA busy_timeout=15000")
        cur.execute("PRAGMA synchronous=NORMAL")
        cur.close()

    return engine


def init_schema(engine: Engine) -> None:
    """Create tables directly (tests). Production uses Alembic migrations."""
    Base.metadata.create_all(engine)
