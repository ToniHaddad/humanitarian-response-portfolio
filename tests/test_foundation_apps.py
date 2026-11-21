"""Homepage, launcher and project layout rules."""

from __future__ import annotations

from collections import Counter
from pathlib import Path

from streamlit.testing.v1 import AppTest

import run
from shared.projects import all_projects

ROOT = Path(__file__).resolve().parents[1]


def test_homepage_renders_twenty_cards_without_errors(db_path: Path) -> None:
    at = AppTest.from_file(str(ROOT / "home.py"), default_timeout=60).run()
    assert not at.exception
    text = " ".join(m.value for m in at.markdown)
    for p in all_projects():
        assert p.title in text
    assert "DEMO" in text
    headers = [h.value for h in at.header]
    assert sum(h[:2] in ("A.", "B.", "C.", "D.", "E.") for h in headers) == 5


def test_homepage_shows_in_progress_for_unbuilt_projects(db_path: Path) -> None:
    at = AppTest.from_file(str(ROOT / "home.py"), default_timeout=60).run()
    text = " ".join(m.value for m in at.markdown)
    unbuilt = [p for p in all_projects() if not p.built]
    assert text.count("In progress") == len(unbuilt)


def test_homepage_reset_needs_confirmation_then_rebuilds(db_path: Path) -> None:
    from shared import db

    with db.transaction() as c:
        c.execute("DELETE FROM calls WHERE call_type = 'information'")
    before = db.scalar("SELECT COUNT(*) FROM calls")
    at = AppTest.from_file(str(ROOT / "home.py"), default_timeout=60).run()
    at.button[0].click().run()  # submit without ticking the box: nothing changes
    assert db.scalar("SELECT COUNT(*) FROM calls") == before
    at.checkbox[0].check()  # form values are sent together with the submit click
    at.button[0].click().run()
    assert not at.exception
    assert db.scalar("SELECT COUNT(*) FROM calls") == 300


def test_launcher_command_uses_project_port() -> None:
    p = all_projects()[1]
    cmd = run.streamlit_command(p.app_path, p.port)
    assert cmd[cmd.index("--server.port") + 1] == "8502"


def test_project_module_names_are_unique() -> None:
    """Every project folder is on sys.path in tests, so module names must not collide."""
    names = Counter()
    for folder in (ROOT / "projects").iterdir():
        for py in folder.glob("*.py"):
            if py.name != "app.py":
                names[py.stem] += 1
    shared = {p.stem for p in (ROOT / "shared").glob("*.py")}
    assert [n for n, c in names.items() if c > 1] == []
    assert not (set(names) & shared)


def test_one_test_file_per_project_naming() -> None:
    for path in (ROOT / "tests").glob("test_p*.py"):
        assert path.name[6:8].isdigit() and path.name[8] == "_", path.name
