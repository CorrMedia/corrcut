"""JSON project save/load with version field."""

from __future__ import annotations

import json
from pathlib import Path

from corrcut.config import PROJECT_FILE_VERSION
from corrcut.domain.project import Project
from corrcut.io.atomic_write import atomic_write_text

PROJECT_SUFFIX = ".corrcut"


def default_project_path_for_source(source_path: Path) -> Path:
    """Project file alongside the media, same basename with the project suffix."""
    return source_path.resolve().with_suffix(PROJECT_SUFFIX)


def save_project(project: Project, path: Path) -> None:
    project.project_path = path.resolve()
    data = project.to_json()
    atomic_write_text(path, json.dumps(data, indent=2))


def load_project(path: Path) -> Project:
    raw = json.loads(path.read_text(encoding="utf-8"))
    version = int(raw.get("project_version", 0))
    if version > PROJECT_FILE_VERSION:
        raise ValueError(f"unsupported project version {version}")
    project = Project.from_json(raw)
    project.project_path = path.resolve()
    return project
