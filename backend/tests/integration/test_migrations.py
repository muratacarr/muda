from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from muda.persistence.db import create_db_engine

BACKEND = Path(__file__).resolve().parents[2]


def test_migrated_schema_enforces_single_active_job(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    url = f"sqlite:///{tmp_path / 'm.db'}"
    monkeypatch.setenv("MUDA_DATABASE_URL", url)
    cfg = Config(str(BACKEND / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND / "migrations"))
    command.upgrade(cfg, "head")

    engine = create_db_engine(url)
    insert = text(
        "INSERT INTO jobs (id, prompt, telegram_user_id, state, cancel_requested,"
        " created_at, updated_at) VALUES (:id, 'p', 1, :st, 0, '2026-01-01', '2026-01-01')"
    )
    with engine.begin() as c:
        c.execute(insert, {"id": "a", "st": "QUEUED"})
        c.execute(insert, {"id": "done", "st": "PUBLISHED"})
    with pytest.raises(IntegrityError), engine.begin() as c:
        c.execute(insert, {"id": "b", "st": "PROCESSING"})
    engine.dispose()
