from collections.abc import Iterator
from pathlib import Path

import pytest
from sqlalchemy import Engine

from muda.persistence.db import create_db_engine, init_schema


@pytest.fixture
def engine(tmp_path: Path) -> Iterator[Engine]:
    eng = create_db_engine(f"sqlite:///{tmp_path / 'test.db'}")
    init_schema(eng)
    yield eng
    eng.dispose()
