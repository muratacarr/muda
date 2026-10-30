import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from muda.backup import BackupError, backup_database

T0 = datetime(2026, 10, 30, 12, 0, 0, tzinfo=UTC)


def _make_db(path: Path) -> None:
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("CREATE TABLE t (id INTEGER PRIMARY KEY, v TEXT)")
    conn.executemany("INSERT INTO t (v) VALUES (?)", [("a",), ("b",), ("c",)])
    conn.commit()
    conn.close()


def test_backup_is_a_consistent_copy(tmp_path: Path) -> None:
    db = tmp_path / "muda.db"
    _make_db(db)
    out = backup_database(db, tmp_path / "backups", now=T0)
    assert out.name == "muda-20261030T120000Z.db"
    conn = sqlite3.connect(out)
    assert conn.execute("PRAGMA integrity_check").fetchone() == ("ok",)
    assert conn.execute("SELECT v FROM t ORDER BY id").fetchall() == [("a",), ("b",), ("c",)]
    conn.close()


def test_backup_includes_uncheckpointed_wal_data(tmp_path: Path) -> None:
    db = tmp_path / "muda.db"
    _make_db(db)
    live = sqlite3.connect(db)  # keeps the WAL open, so recent writes live only in the -wal file
    live.execute("INSERT INTO t (v) VALUES ('wal-only')")
    live.commit()
    out = backup_database(db, tmp_path / "backups", now=T0)
    conn = sqlite3.connect(out)
    assert ("wal-only",) in conn.execute("SELECT v FROM t").fetchall()
    conn.close()
    live.close()


def test_old_backups_are_pruned(tmp_path: Path) -> None:
    db = tmp_path / "muda.db"
    _make_db(db)
    dest = tmp_path / "backups"
    for i in range(5):
        backup_database(db, dest, keep=3, now=T0 + timedelta(hours=i))
    names = sorted(p.name for p in dest.glob("muda-*.db"))
    assert names == [
        "muda-20261030T140000Z.db",
        "muda-20261030T150000Z.db",
        "muda-20261030T160000Z.db",
    ]


def test_pruning_never_touches_unrelated_files(tmp_path: Path) -> None:
    db = tmp_path / "muda.db"
    _make_db(db)
    dest = tmp_path / "backups"
    dest.mkdir()
    (dest / "notes.txt").write_text("keep me")
    for i in range(3):
        backup_database(db, dest, keep=1, now=T0 + timedelta(hours=i))
    assert (dest / "notes.txt").exists()


def test_missing_database_fails_and_leaves_no_partial_file(tmp_path: Path) -> None:
    dest = tmp_path / "backups"
    with pytest.raises(BackupError):
        backup_database(tmp_path / "nope.db", dest, now=T0)
    assert not list(dest.glob("*")) if dest.exists() else True


def test_corrupt_source_is_reported(tmp_path: Path) -> None:
    db = tmp_path / "muda.db"
    db.write_bytes(b"this is not a sqlite database" * 100)
    dest = tmp_path / "backups"
    with pytest.raises(BackupError):
        backup_database(db, dest, now=T0)
    assert not list(dest.glob("muda-*.db"))


def test_keep_must_be_positive(tmp_path: Path) -> None:
    db = tmp_path / "muda.db"
    _make_db(db)
    with pytest.raises(ValueError):
        backup_database(db, tmp_path / "b", keep=0, now=T0)
