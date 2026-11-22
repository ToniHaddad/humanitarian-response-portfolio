"""Smoke test of the running Docker server-mode stack. One line per check; exit 1 if any check fails.

    python3 scripts/server_smoke.py                 # from the repo folder, with the stack up and a .env file
    python3 scripts/server_smoke.py --no-restart    # skip the restart check (does not touch the running stack)

Reads PORTFOLIO_STAFF_USERNAME, PORTFOLIO_STAFF_PASSWORD, PORTFOLIO_API_TOKEN and SERVER_BIND_ADDRESS
from the environment, falling back to the .env file. Needs only Python 3 and the docker CLI.
Checks: the homepage and the twenty project ports, the API and its docs, that server mode refuses an
unauthenticated write and accepts a logged-in one (and audits it), the scheduler heartbeat and its
start-up jobs, fresh simulated positions, a backup file and a restore into a scratch path, that no port
is published on all interfaces, and that the API comes back after `docker compose restart api`.
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import subprocess
import time
import urllib.error
import urllib.request
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FAILED: list[str] = []


def report(ok: bool, name: str, detail: str = "") -> bool:
    print(f"{'PASS' if ok else 'FAIL'}  {name}" + (f"  ({detail})" if detail else ""), flush=True)
    if not ok:
        FAILED.append(name)
    return ok


def load_env() -> dict[str, str]:
    values: dict[str, str] = {}
    env_file = ROOT / ".env"
    if env_file.exists():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                key, _, value = line.partition("=")
                values[key.strip()] = value.strip()
    values.update({k: v for k, v in os.environ.items() if k.startswith(("PORTFOLIO_", "SERVER_"))})
    return values


def http(url: str, method: str = "GET", headers: dict[str, str] | None = None, body: dict | None = None, timeout: float = 10):
    """(status, text). HTTP errors return their status instead of raising; connection errors return (0, message)."""
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        url, data=data, method=method, headers={**(headers or {}), **({"Content-Type": "application/json"} if body is not None else {})}
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode("utf-8", "replace")
    except OSError as exc:
        return 0, str(exc)


def compose(*args: str, check: bool = False) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["docker", "compose", *args], cwd=ROOT, capture_output=True, text=True, check=check)


def container_python(service: str, code: str, *argv: str) -> subprocess.CompletedProcess[str]:
    return compose("exec", "-T", service, "python", "-c", code, *argv)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--host", default="127.0.0.1", help="address the published ports answer on (default 127.0.0.1)")
    parser.add_argument("--no-restart", action="store_true", help="skip the `docker compose restart api` check")
    args = parser.parse_args()
    env = load_env()
    host = args.host
    api = f"http://{host}:8600"
    user, password, token = (env.get(k, "") for k in ("PORTFOLIO_STAFF_USERNAME", "PORTFOLIO_STAFF_PASSWORD", "PORTFOLIO_API_TOKEN"))
    basic = "Basic " + base64.b64encode(f"{user}:{password}".encode()).decode()

    # 1. Streamlit apps
    ports = [8500, *range(8501, 8521)]
    bad = [p for p in ports if http(f"http://{host}:{p}/_stcore/health", timeout=15)[1].strip() != "ok"]
    report(
        not bad,
        "homepage 8500 and projects 8501-8520 answer /_stcore/health",
        f"{len(ports) - len(bad)}/{len(ports)} ok" + (f", not ok: {bad}" if bad else ""),
    )

    # 2. API health and docs
    status, text = http(f"{api}/health")
    health = json.loads(text) if status == 200 else {}
    report(
        status == 200 and health.get("healthy") is True, "API /health reports healthy", f"HTTP {status}, healthy={health.get('healthy')}"
    )
    if status == 200 and not health.get("healthy"):
        print("      services:", [(s["service_name"], s["status"], s["silent"]) for s in health.get("services", [])])
        print("      jobs:", [(j["job_name"], j["last_run_status"], j["overdue"]) for j in health.get("jobs", [])])
    d_status, d_text = http(f"{api}/reference")
    o_status, o_text = http(f"{api}/openapi.json")
    docs_status, docs_text = http(f"{api}/docs")
    report(
        d_status == 200 and o_status == 200 and docs_status == 200,
        "API documentation pages load (/docs, /reference, /openapi.json)",
        f"{docs_status}/{d_status}/{o_status}",
    )

    # 3. server mode: write refused without credentials, accepted with them, and audited
    probe_body = {"vehicle_id": "VEH-03", "x_km": 50.0, "y_km": 50.0, "speed_kmh": 10.0}
    status, _ = http(f"{api}/positions", "POST", {"X-Staff-Id": "CRD-01"}, probe_body)
    report(status == 401, "server mode: write without credentials is refused", f"HTTP {status}")
    status, _ = http(
        f"{api}/positions",
        "POST",
        {"X-Staff-Id": "CRD-01", "Authorization": "Basic " + base64.b64encode(b"nobody:wrong").decode()},
        probe_body,
    )
    report(status == 401, "server mode: write with a wrong login is refused", f"HTTP {status}")
    status, text = http(f"{api}/positions", "POST", {"X-Staff-Id": "CRD-01", "Authorization": basic}, probe_body)
    created = json.loads(text) if status == 201 else {}
    report(status == 201, "server mode: write with the staff login succeeds", f"HTTP {status}")
    status, text = http(f"{api}/positions", "POST", {"X-Staff-Id": "CRD-01", "Authorization": f"Bearer {token}"}, probe_body)
    report(status == 201, "server mode: write with the API token succeeds", f"HTTP {status}")
    status, text = http(f"{api}/audit?channel=api&entity_table=position_reports&limit=20")
    entries = json.loads(text) if status == 200 else []
    report(
        bool(created) and any(e["entity_id"] == str(created.get("report_id")) and e["actor_id"] == "CRD-01" for e in entries),
        "the write appears in the audit log through the API",
        f"report {created.get('report_id')}",
    )

    # 4. scheduler alive, start-up jobs ran after the stack started
    sched = compose("ps", "-q", "scheduler").stdout.strip()
    started = (
        subprocess.run(["docker", "inspect", "-f", "{{.State.StartedAt}}", sched], capture_output=True, text=True).stdout.strip()
        if sched
        else ""
    )
    start_epoch = 0.0
    if started:
        start_epoch = datetime.fromisoformat(started[:19]).replace(tzinfo=UTC).timestamp()
    local = container_python(
        "api",
        "import sys,datetime;print(datetime.datetime.fromtimestamp(float(sys.argv[1])).isoformat(timespec='seconds'))",
        str(start_epoch),
    ).stdout.strip()
    start_local = datetime.fromisoformat(local) if local else None
    report(start_local is not None, "stack start time read from the scheduler container", f"{local} (container local time)")
    if start_local:
        svc = {s["service_name"]: s for s in health.get("services", [])}
        beat = svc.get("scheduler")
        report(
            bool(beat) and not beat["silent"] and beat["minutes_since_seen"] is not None and beat["minutes_since_seen"] < 2,
            "scheduler heartbeat is recent",
            f"{beat and beat['minutes_since_seen']} min ago",
        )
        jobs = {j["job_name"]: j for j in health.get("jobs", [])}
        for name in ("position_simulator", "alert_evaluation"):
            j = jobs.get(name) or {}
            ok = (
                bool(j.get("last_success_at"))
                and datetime.fromisoformat(j["last_success_at"]) >= start_local
                and j.get("last_run_status") == "success"
            )
            report(ok, f"scheduler job {name}: successful run recorded after start", f"last success {j.get('last_success_at')}")
        status, text = http(f"{api}/vehicles/positions")
        newest = max(
            (v["reported_at"] for v in (json.loads(text) if status == 200 else []) if v["simulated"] and v["reported_at"]), default=None
        )
        report(
            bool(newest) and datetime.fromisoformat(newest) >= start_local,
            "positions are arriving: newest simulated report is newer than the start",
            f"newest {newest}",
        )

    # 5. backup file and restore into a scratch path
    code = (
        "import glob,os,subprocess,sys,sqlite3;"
        "files=sorted(glob.glob('/backups/portfolio-????????-??????.db'));"
        "print('backups',len(files));"
        "assert files,'no backup file';"
        "t='/tmp/restore-check/portfolio.db';"
        "r=subprocess.run([sys.executable,'scripts/restore_db.py','--backup',files[-1],'--target',t,'--yes'],capture_output=True,text=True);"
        "print(r.stdout.strip(),r.stderr.strip());"
        "assert r.returncode==0;"
        "c=sqlite3.connect(t);print('integrity',c.execute('PRAGMA integrity_check').fetchone()[0],'projects tables',c.execute(\"select count(*) from sqlite_master where type='table'\").fetchone()[0])"
    )
    done = container_python("backup", code)
    report(
        done.returncode == 0 and "integrity ok" in done.stdout,
        "backup file exists and restore_db.py restores it into a scratch path",
        (done.stdout + done.stderr).strip().replace("\n", " | ")[-300:],
    )

    # 6. no port on all interfaces
    ps = compose("ps", "--format", "json").stdout.strip()
    rows = json.loads(ps) if ps.startswith("[") else [json.loads(line) for line in ps.splitlines() if line.strip()]
    wide, count = [], 0
    for row in rows:
        for pub in row.get("Publishers") or []:
            if pub.get("PublishedPort"):
                count += 1
                if pub.get("URL") in ("0.0.0.0", "::", ""):
                    wide.append(f"{row.get('Service')}:{pub.get('PublishedPort')} on {pub.get('URL')!r}")
    binds = {pub.get("URL") for row in rows for pub in row.get("Publishers") or [] if pub.get("PublishedPort")}
    out = compose("port", "api", "8600").stdout.strip()
    report(
        count > 0 and not wide,
        "no port is published on all interfaces",
        f"{count} published ports, bound to {sorted(binds)}; `docker compose port api 8600` -> {out}",
    )

    # 7. restart the API
    if args.no_restart:
        print("SKIP  API recovers after `docker compose restart api` (--no-restart)")
    else:
        compose("restart", "api")
        ok, code = False, 0
        for _ in range(60):
            code, text = http(f"{api}/health", timeout=5)
            if code == 200:
                ok = True
                break
            time.sleep(3)
        report(ok, "after `docker compose restart api` the API becomes healthy again", f"last HTTP {code}")

    print()
    if FAILED:
        print(f"{len(FAILED)} check(s) FAILED: " + "; ".join(FAILED))
        return 1
    print("All smoke checks passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
