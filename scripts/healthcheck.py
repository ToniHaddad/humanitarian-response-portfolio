"""Container health checks (used by docker-compose.yml). Exit 0 = healthy, 1 = unhealthy.

python scripts/healthcheck.py http http://localhost:8600/health   # URL answers 200
python scripts/healthcheck.py apps                                # homepage and every built app answer
python scripts/healthcheck.py heartbeat scheduler [MAX_MINUTES]   # service seen recently in the database
python scripts/healthcheck.py backup MAX_AGE_SECONDS              # a backup newer than this exists
"""

from __future__ import annotations

import os
import sqlite3
import sys
import time
import urllib.request
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def url_ok(url: str, timeout: float = 5.0) -> bool:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            return resp.status == 200
    except OSError:
        return False


def apps_ok() -> bool:
    sys.path.insert(0, str(ROOT))
    from shared import ports
    from shared.projects import all_projects

    wanted = [ports.HOMEPAGE_PORT] + [p.port for p in all_projects() if p.built and p.app_path.exists()]
    return all(url_ok(f"http://localhost:{port}/_stcore/health") for port in wanted)


def heartbeat_ok(service: str, max_minutes: float = 5.0) -> bool:
    db = Path(os.environ.get("PORTFOLIO_DB_PATH") or ROOT / "data" / "portfolio.db")
    try:
        conn = sqlite3.connect(f"{db.resolve().as_uri()}?mode=ro", uri=True, timeout=5)
        try:
            row = conn.execute("SELECT last_seen_at FROM service_heartbeats WHERE service_name = ?", (service,)).fetchone()
        finally:
            conn.close()
        return bool(row) and (datetime.now() - datetime.fromisoformat(row[0])).total_seconds() <= max_minutes * 60
    except (sqlite3.Error, ValueError):
        return False


def backup_ok(max_age_seconds: float) -> bool:
    folder = Path(os.environ.get("PORTFOLIO_BACKUP_DIR") or ROOT / "data" / "backups")
    copies = sorted(folder.glob("*-????????-??????.db"))
    return bool(copies) and time.time() - copies[-1].stat().st_mtime <= max_age_seconds


def main(argv: list[str]) -> int:
    if not argv:
        print(__doc__)
        return 1
    kind, rest = argv[0], argv[1:]
    if kind == "http" and rest:
        ok = url_ok(rest[0])
    elif kind == "apps":
        ok = apps_ok()
    elif kind == "heartbeat" and rest:
        ok = heartbeat_ok(rest[0], float(rest[1]) if len(rest) > 1 else 5.0)
    elif kind == "backup" and rest:
        ok = backup_ok(float(rest[0]))
    else:
        print(__doc__)
        return 1
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
