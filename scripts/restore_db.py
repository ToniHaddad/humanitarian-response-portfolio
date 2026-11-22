"""Restore the SQLite database from a backup made by scripts/backup_db.py.

    python scripts/restore_db.py --latest --yes
    python scripts/restore_db.py --backup /backups/portfolio-20261116-090000.db --yes

STOP every service that uses the database first (see DEPLOYMENT.md). The backup is checked
first; the database being replaced is kept next to it as <name>.before-restore-<time>.
"""

from __future__ import annotations

import argparse
import os
import sqlite3
import sys
from datetime import datetime
from pathlib import Path


def latest_backup(dest_dir: Path, stem: str) -> Path:
    copies = sorted(Path(dest_dir).glob(f"{stem}-????????-??????.db"))
    if not copies:
        raise FileNotFoundError(f"No backups named {stem}-*.db in {dest_dir}")
    return copies[-1]


def _open_readonly(path: Path) -> sqlite3.Connection:
    return sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True)


def restore_database(backup: Path, target: Path, now: datetime | None = None) -> Path | None:
    """Replace `target` with `backup`. Returns the path the old database was kept at (None if there was none)."""
    backup, target = Path(backup), Path(target)
    if not backup.exists():
        raise FileNotFoundError(f"No backup at {backup}")
    check = _open_readonly(backup)
    try:
        result = check.execute("PRAGMA integrity_check").fetchone()[0]
    finally:
        check.close()
    if result != "ok":
        raise RuntimeError(f"{backup} failed its integrity check: {result}")

    target.parent.mkdir(parents=True, exist_ok=True)
    partial = target.with_name(f".{target.name}.restoring")
    partial.unlink(missing_ok=True)
    src = _open_readonly(backup)
    dst = sqlite3.connect(partial)
    try:
        src.backup(dst)
    finally:
        dst.close()
        src.close()

    kept: Path | None = None
    if target.exists():
        kept = target.with_name(f"{target.name}.before-restore-{(now or datetime.now()):%Y%m%d-%H%M%S}")
        os.replace(target, kept)
    for suffix in ("-wal", "-shm"):  # leftovers of the old database must not be replayed onto the restored one
        target.with_name(target.name + suffix).unlink(missing_ok=True)
    os.replace(partial, target)
    return kept


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Restore the portfolio database from a backup.")
    parser.add_argument("--target", default=os.environ.get("PORTFOLIO_DB_PATH") or "data/portfolio.db", help="database file to replace")
    parser.add_argument("--dest", default=os.environ.get("PORTFOLIO_BACKUP_DIR") or "data/backups", help="backup folder (for --latest)")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--backup", help="backup file to restore")
    group.add_argument("--latest", action="store_true", help="restore the newest backup in the backup folder")
    parser.add_argument("--yes", action="store_true", help="confirm that the services using the database are stopped")
    args = parser.parse_args(argv)

    target = Path(args.target)
    try:
        backup = latest_backup(Path(args.dest), target.stem) if args.latest else Path(args.backup)
    except FileNotFoundError as exc:
        print(exc, file=sys.stderr)
        return 1
    if not args.yes:
        print(f"Would replace {target} with {backup}. Stop the services, then run again with --yes.")
        return 2
    try:
        kept = restore_database(backup, target)
    except (FileNotFoundError, RuntimeError, sqlite3.Error) as exc:
        print(f"Restore failed, nothing changed: {exc}", file=sys.stderr)
        return 1
    print(f"Restored {target} from {backup}." + (f" The previous database is kept as {kept}." if kept else ""))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
