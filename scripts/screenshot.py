"""Capture a real screenshot of a running app with the installed Chrome or Edge.

    python scripts/screenshot.py --project 02 --out projects/02-call-center-calls-and-complaints/screenshot.png
    python scripts/screenshot.py --project 02 --out shot.png --db data/agent02.db --wait-text "Calls received"
    python scripts/screenshot.py --home --out docs/homepage.png

It starts the app on its own port (8500 + NN, or 8500 for the homepage) against the
given database (default: PORTFOLIO_DB_PATH or data/portfolio.db, seeded if missing),
waits until the page has rendered, captures it, and stops the app. No browser is
downloaded: Playwright drives the Chrome or Edge already installed.
"""

from __future__ import annotations

import argparse
import os
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from shared import config, ports, seed  # noqa: E402
from shared.projects import get_project  # noqa: E402


def port_in_use(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.5)
        return s.connect_ex(("127.0.0.1", port)) == 0


def wait_healthy(port: int, timeout: float) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(f"http://localhost:{port}/_stcore/health", timeout=2) as resp:
                if resp.status == 200:
                    return
        except OSError:
            time.sleep(0.5)
    raise TimeoutError(f"App on port {port} did not become healthy within {timeout:.0f} s")


def capture(url: str, out: Path, width: int, height: int, full_page: bool, wait_text: str | None, settle: float) -> None:
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        browser = None
        errors = []
        for channel in ("chrome", "msedge"):
            try:
                browser = pw.chromium.launch(channel=channel, headless=True)
                break
            except Exception as exc:  # noqa: BLE001 - try the next installed browser
                errors.append(f"{channel}: {exc}")
        if browser is None:
            raise RuntimeError("No installed Chrome or Edge could be started:\n" + "\n".join(errors))
        page = browser.new_page(viewport={"width": width, "height": height})
        page.goto(url, wait_until="networkidle", timeout=60000)
        page.wait_for_selector('[data-testid="stAppViewContainer"]', timeout=60000)
        page.wait_for_selector("h1", timeout=60000)
        if wait_text:
            page.get_by_text(wait_text).first.wait_for(timeout=60000)
        # Wait until Streamlit reports it is no longer running the script.
        deadline = time.time() + 60
        while time.time() < deadline:
            if page.locator('[data-testid="stStatusWidget"]').count() == 0:
                break
            time.sleep(0.5)
        time.sleep(settle)
        if full_page:
            # Streamlit scrolls inside its own container, so grow the viewport to the content height.
            content_height = page.evaluate(
                """() => Math.max(...['[data-testid="stMain"]', '[data-testid="stAppViewContainer"]', 'section.main']
                    .map(s => document.querySelector(s)).filter(Boolean).map(e => e.scrollHeight), document.body.scrollHeight)"""
            )
            page.set_viewport_size({"width": width, "height": min(int(content_height) + 40, 12000)})
            time.sleep(1.5)
        out.parent.mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(out), full_page=full_page)
        browser.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Screenshot a project app or the homepage.")
    target = parser.add_mutually_exclusive_group(required=True)
    target.add_argument("--project", help="project number, e.g. 02")
    target.add_argument("--home", action="store_true", help="the portfolio homepage")
    parser.add_argument("--out", required=True, help="output PNG path")
    parser.add_argument("--db", help="database file to run against (seeded if missing)")
    parser.add_argument("--port", type=int, help="override the port")
    parser.add_argument("--width", type=int, default=1400)
    parser.add_argument("--height", type=int, default=1000)
    parser.add_argument("--full-page", action="store_true", help="capture the whole scrolling page")
    parser.add_argument("--wait-text", help="wait until this text is visible before capturing")
    parser.add_argument("--query", help="URL query string to open the app with, e.g. q=heating+fuel")
    parser.add_argument("--settle", type=float, default=3.0, help="extra seconds to wait after rendering")
    args = parser.parse_args(argv)

    env = os.environ.copy()
    if args.db:
        env[config.DB_ENV_VAR] = str(Path(args.db).resolve())
        os.environ[config.DB_ENV_VAR] = env[config.DB_ENV_VAR]
    seed.ensure_database()

    if args.home:
        script, port = ROOT / "home.py", args.port or ports.HOMEPAGE_PORT
    else:
        project = get_project(args.project)
        script, port = project.app_path, args.port or project.port
        if not script.exists():
            raise SystemExit(f"{script} does not exist")
    if port_in_use(port):
        raise SystemExit(f"Port {port} is already in use; stop that app or pass --port")

    cmd = [
        sys.executable,
        "-m",
        "streamlit",
        "run",
        str(script),
        "--server.port",
        str(port),
        "--server.address",
        "localhost",
        "--server.headless",
        "true",
        "--browser.gatherUsageStats",
        "false",
    ]
    proc = subprocess.Popen(cmd, cwd=ROOT, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        wait_healthy(port, 90)
        capture(f"http://localhost:{port}" + (f"/?{args.query}" if args.query else ""), Path(args.out), args.width, args.height, args.full_page, args.wait_text, args.settle)
        print(f"Saved {args.out}")
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=15)
        except subprocess.TimeoutExpired:
            proc.kill()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
