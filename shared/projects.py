"""Project registry, read from each project's own marker file.

Every project folder holds a `project.toml`. The homepage and the launcher read
these files, so a project agent marks its project built by editing only its own
marker (`status = "built"`), never a shared list.
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path

from shared import config, ports

GROUPS: dict[str, str] = {
    "A": "Call center and casework",
    "B": "Data intake and quality",
    "C": "Needs and supply chain",
    "D": "Field operations",
    "E": "Operations room, reporting, and platform",
}

VALID_STATUSES = ("in_progress", "built")


@dataclass(frozen=True)
class Project:
    number: int
    slug: str
    title: str
    group: str
    status: str
    user: str
    task: str
    folder: Path

    @property
    def code(self) -> str:
        return f"{self.number:02d}"

    @property
    def built(self) -> bool:
        return self.status == "built"

    @property
    def port(self) -> int:
        return ports.project_port(self.number)

    @property
    def url(self) -> str:
        return ports.project_url(self.number)

    @property
    def app_path(self) -> Path:
        return self.folder / "app.py"


def load_project(folder: Path) -> Project:
    data = tomllib.loads((folder / "project.toml").read_text(encoding="utf-8"))
    status = str(data.get("status", "in_progress"))
    if status not in VALID_STATUSES:
        raise ValueError(f"{folder.name}/project.toml: status must be one of {VALID_STATUSES}")
    return Project(
        number=int(data["number"]),
        slug=str(data["slug"]),
        title=str(data["title"]),
        group=str(data["group"]),
        status=status,
        user=str(data["user"]),
        task=str(data["task"]),
        folder=folder,
    )


def all_projects(projects_dir: Path | None = None) -> list[Project]:
    base = projects_dir or config.PROJECTS_DIR
    found = [load_project(p) for p in sorted(base.iterdir()) if (p / "project.toml").exists()]
    return sorted(found, key=lambda p: p.number)


def get_project(number: int | str) -> Project:
    n = int(number)
    for p in all_projects():
        if p.number == n:
            return p
    raise KeyError(f"No project {number}")
