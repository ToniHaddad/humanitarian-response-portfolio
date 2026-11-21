"""One-command launcher for the portfolio.

    python run.py                 # build the demo database if needed, start the homepage (port 8500)
                                  # and every built project (port 8500 + NN), then open the homepage
    python run.py 02 04           # start only these projects (ports 8502 and 8504)
    python run.py --home-only     # start only the homepage
    python run.py --reset         # rebuild the demo database and exit
    python run.py --list          # list the projects, their ports and status

Stop everything with Ctrl+C. Set PORTFOLIO_DB_PATH to run against another database file.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
import urllib.request
import webbrowser
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from shared import config, ports, seed  # noqa: E402
from shared.projects import Project, all_projects, get_project  # noqa: E402

BIND_ADDRESS = os.environ.get("PORTFOLIO_BIND_ADDRESS", "localhost")


def streamlit_command(script: Path, port: int) -> list[str]:
    return [
        sys.executable,
        "-m",
        "streamlit",
        "run",
        str(script),
        "--server.port",
        str(port),
        "--server.address",
        BIND_ADDRESS,
        "--server.headless",
        "true",
        "--browser.gatherUsageStats",
        "false",
    ]


def wait_until_up(port: int, timeout: float = 60.0) -> bool:
    url = f"http://localhost:{port}/_stcore/health"
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=2) as resp:
                if resp.status == 200:
                    return True
        except OSError:
            time.sleep(0.5)
    return False


def selected_projects(codes: list[str]) -> list[Project]:
    if codes:
        return [get_project(c) for c in codes]
    return [p for p in all_projects() if p.built]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Launch the portfolio or single projects.")
    parser.add_argument("projects", nargs="*", help="project numbers to start, e.g. 02 04 (default: homepage + all built)")
    parser.add_argument("--reset", action="store_true", help="rebuild the demo database and exit")
    parser.add_argument("--list", action="store_true", help="list projects and exit")
    parser.add_argument("--home-only", action="store_true", help="start only the homepage")
    parser.add_argument("--no-browser", action="store_true", help="do not open the homepage in a browser")
    args = parser.parse_args(argv)

    if args.list:
        for p in all_projects():
            print(f"{p.code}  port {p.port}  {p.status:12s} {p.folder.name}")
        return 0
    if args.reset:
        print(f"Demo database rebuilt: {seed.reset_database()}")
        return 0

    seed.ensure_database()
    print(f"Database: {config.db_path()}")
    to_start: list[tuple[str, Path, int]] = []
    if not args.projects:
        to_start.append(("homepage", ROOT / "home.py", ports.HOMEPAGE_PORT))
    if not args.home_only:
        for p in selected_projects(args.projects):
            if not p.app_path.exists():
                print(f"Skipping {p.code}: {p.app_path.relative_to(ROOT)} does not exist yet")
                continue
            to_start.append((f"{p.code} {p.title}", p.app_path, p.port))

    procs: list[subprocess.Popen[bytes]] = []
    try:
        for name, script, port in to_start:
            procs.append(subprocess.Popen(streamlit_command(script, port), cwd=ROOT))
            print(f"Starting {name} on http://localhost:{port}")
        for name, _script, port in to_start:
            ok = wait_until_up(port)
            print(f"{'Ready' if ok else 'NOT RESPONDING'}: {name} on http://localhost:{port}")
        if to_start and not args.no_browser:
            webbrowser.open(f"http://localhost:{to_start[0][2]}")
        print("Press Ctrl+C to stop.")
        while any(p.poll() is None for p in procs):
            time.sleep(1)
    except KeyboardInterrupt:
        print("Stopping...")
    finally:
        for p in procs:
            if p.poll() is None:
                p.terminate()
        for p in procs:
            try:
                p.wait(timeout=10)
            except subprocess.TimeoutExpired:
                p.kill()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
