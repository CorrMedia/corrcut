"""Persist recently opened project files."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from corrcut.config import app_settings

_SETTINGS_ORG = "corrcut"
_SETTINGS_APP = "CorrCUT"
_KEY_RECENT_PATHS = "recent_projects/paths"
_MAX_RECENT = 12


@dataclass(frozen=True)
class RecentProjectEntry:
    path: Path

    @property
    def display_name(self) -> str:
        return self.path.name

    @property
    def exists(self) -> bool:
        return self.path.is_file()


def _settings():
    return app_settings()


def list_recent_projects() -> list[RecentProjectEntry]:
    raw = _settings().value(_KEY_RECENT_PATHS, [])
    if raw is None:
        return []
    if isinstance(raw, str):
        paths = [raw] if raw else []
    else:
        paths = list(raw)
    entries: list[RecentProjectEntry] = []
    seen: set[str] = set()
    for item in paths:
        text = str(item).strip()
        if not text or text in seen:
            continue
        seen.add(text)
        entries.append(RecentProjectEntry(path=Path(text)))
    return entries


def add_recent_project(path: Path) -> None:
    resolved = path.resolve()
    current = [entry.path for entry in list_recent_projects() if entry.path != resolved]
    current.insert(0, resolved)
    trimmed = [str(p) for p in current[:_MAX_RECENT]]
    _settings().setValue(_KEY_RECENT_PATHS, trimmed)


def remove_recent_project(path: Path) -> None:
    resolved = path.resolve()
    kept = [str(entry.path) for entry in list_recent_projects() if entry.path != resolved]
    _settings().setValue(_KEY_RECENT_PATHS, kept)
