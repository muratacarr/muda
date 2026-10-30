"""Online SQLite backup: consistent while the app runs, verified, written atomically.

Usage: python -m muda.backup   (reads DATABASE_URL, BACKUP_DIR, BACKUP_KEEP)
"""

import logging
import os
import sqlite3
import sys
from datetime import UTC, datetime
from pathlib import Path

logger = logging.getLogger("muda.backup")


class BackupError(Exception):
    pass


def backup_database(
    db_path: Path, dest_dir: Path, *, keep: int = 14, now: datetime | None = None
) -> Path:
    if keep < 1:
        raise ValueError("keep must be >= 1")
    if not db_path.is_file():
        raise BackupError(f"database not found: {db_path}")
    stamp = (now or datetime.now(UTC)).strftime("%Y%m%dT%H%M%SZ")
    dest_dir.mkdir(parents=True, exist_ok=True)
    target = dest_dir / f"muda-{stamp}.db"
    partial = dest_dir / f".muda-{stamp}.partial"
    source = dest = None
    try:
        source = sqlite3.connect(db_path)
        dest = sqlite3.connect(partial)
        source.backup(dest)  # SQLite online backup API: safe against concurrent writers
        if dest.execute("PRAGMA integrity_check").fetchone() != ("ok",):
            raise BackupError("integrity check failed on the copy")
        dest.close()
        dest = None
        os.replace(partial, target)  # atomic: a backup file is either complete or absent
    except sqlite3.Error as exc:
        raise BackupError(f"backup failed: {exc}") from exc
    finally:
        if dest is not None:
            dest.close()
        if source is not None:
            source.close()
        partial.unlink(missing_ok=True)
    _prune(dest_dir, keep)
    return target


def _prune(dest_dir: Path, keep: int) -> None:
    backups = sorted(dest_dir.glob("muda-????????T??????Z.db"))
    for old in backups[:-keep]:
        old.unlink()


def main() -> int:
    url = os.environ.get("DATABASE_URL", "sqlite:////data/muda.db")
    if not url.startswith("sqlite:///"):
        print("DATABASE_URL must be a sqlite URL", file=sys.stderr)
        return 2
    db_path = Path(url.removeprefix("sqlite:///"))
    dest = Path(os.environ.get("BACKUP_DIR", "/data/backups"))
    try:
        out = backup_database(db_path, dest, keep=int(os.environ.get("BACKUP_KEEP", "14")))
    except (BackupError, ValueError) as exc:
        print(f"backup failed: {exc}", file=sys.stderr)
        return 1
    print(f"backup written: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
