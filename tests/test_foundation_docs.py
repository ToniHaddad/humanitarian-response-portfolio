"""The root README: its links resolve, it indexes every project, and every project folder is complete."""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
README = ROOT / "README.md"
PROJECT_DIRS = sorted(p for p in (ROOT / "projects").iterdir() if p.is_dir() and (p / "project.toml").exists())
LINK = re.compile(r"!?\[[^\]]*\]\(([^)\s]+)\)")


def _relative_targets() -> list[str]:
    text = README.read_text(encoding="utf-8")
    targets = []
    for raw in LINK.findall(text):
        if re.match(r"^[a-z][a-z0-9+.-]*:", raw) or raw.startswith("#"):
            continue
        targets.append(raw.split("#", 1)[0])
    return targets


def test_readme_has_relative_links() -> None:
    assert len(_relative_targets()) >= 20


@pytest.mark.parametrize("target", sorted(set(_relative_targets())))
def test_readme_relative_link_exists(target: str) -> None:
    assert (ROOT / target).exists(), f"README.md links to {target}, which does not exist"


def test_twenty_project_folders() -> None:
    assert len(PROJECT_DIRS) == 20


def test_index_lists_every_project_folder() -> None:
    text = README.read_text(encoding="utf-8")
    missing = [p.name for p in PROJECT_DIRS if f"(projects/{p.name})" not in text]
    assert not missing, f"README.md index lacks: {missing}"


@pytest.mark.parametrize("folder", PROJECT_DIRS, ids=lambda p: p.name)
def test_project_folder_is_complete(folder: Path) -> None:
    assert (folder / "README.md").is_file()
    assert (folder / "app.py").is_file()
    shot = folder / "screenshot.png"
    assert shot.is_file() and shot.stat().st_size > 10_000, f"{shot} missing or under 10 kB"
    meta = tomllib.loads((folder / "project.toml").read_text(encoding="utf-8"))
    assert meta["status"] == "built"


def test_homepage_screenshot_exists() -> None:
    assert (ROOT / "docs" / "homepage.png").stat().st_size > 10_000
