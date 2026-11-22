"""Back up the SQLite database with SQLite's online backup API.

    python scripts/backup_db.py                        # one backup now, keep the newest 14
    python scripts/backup_db.py --keep 7 --dest /backups
    python scripts/backup_db.py --every 21600          # keep running: one backup every 6 hours

Settings come from the options, then from the environment (PORTFOLIO_DB_PATH,
PORTFOLIO_BACKUP_DIR, BACKUP_KEEP), then from defaults. Each copy is written under a
temporary name, checked with PRAGMA integrity_check, and only then renamed to
<name>-YYYYMMDD-HHMMSS.db, so a half-written file is never mistaken for a backup.
Timestamps use the local clock (TZ), like the rest of the database.
"""

from __future__ import annotations

import argparse
import os
import sqlite3
import sys
import time
from datetime import datetime
from pathlib import Path

DEFAULT_DB = Path("data/portfolio.db")
DEFAULT_DEST = Path("data/backups")
DEFAULT_KEEP = 14


def backup_database(source: Path, dest_dir: Path, keep: int = DEFAULT_KEEP, now: datetime | None = None) -> Path:
    """Write a verified copy of `source` into `dest_dir`; keep only the newest `keep` copies. Returns the new file."""
    source, dest_dir = Path(source), Path(dest_dir)
    if not source.exists():
        raise FileNotFoundError(f"No database at {source}")
    dest_dir.mkdir(parents=True, exist_ok=True)
    stamp = (now or datetime.now()).strftime("%Y%m%d-%H%M%S")
    target = dest_dir / f"{source.stem}-{stamp}.db"
    partial = dest_dir / f".{target.name}.partial"
    partial.unlink(missing_ok=True)
    src = sqlite3.connect(source, timeout=30)
    dst = sqlite3.connect(partial)
    try:
        src.backup(dst)
        result = dst.execute("PRAGMA integrity_check").fetchone()[0]
        if result != "ok":
            raise RuntimeError(f"Backup failed its integrity check: {result}")
    except BaseException:
        dst.close()
        src.close()
        partial.unlink(missing_ok=True)
        raise
    dst.close()
    src.close()
    os.replace(partial, target)
    if keep > 0:
        copies = sorted(dest_dir.glob(f"{source.stem}-????????-??????.db"))
        for old in copies[:-keep]:
            old.unlink(missing_ok=True)
    return target


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, "").strip() or default)
    except ValueError:
        return default


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Back up the portfolio database (SQLite online backup).")
    parser.add_argument("--source", default=os.environ.get("PORTFOLIO_DB_PATH") or str(DEFAULT_DB), help="database file")
    parser.add_argument("--dest", default=os.environ.get("PORTFOLIO_BACKUP_DIR") or str(DEFAULT_DEST), help="backup folder")
    parser.add_argument("--keep", type=int, default=_env_int("BACKUP_KEEP", DEFAULT_KEEP), help="copies to keep (default 14)")
    parser.add_argument("--every", type=int, default=0, metavar="SECONDS", help="repeat forever with this pause; 0 = once")
    args = parser.parse_args(argv)

    while True:
        try:
            target = backup_database(Path(args.source), Path(args.dest), args.keep)
            print(f"{datetime.now():%Y-%m-%d %H:%M:%S} backup written: {target}", flush=True)
        except Exception as exc:
            print(f"{datetime.now():%Y-%m-%d %H:%M:%S} backup FAILED: {exc}", file=sys.stderr, flush=True)
            if args.every <= 0:
                return 1
        if args.every <= 0:
            return 0
        time.sleep(args.every)


if __name__ == "__main__":
    raise SystemExit(main())
