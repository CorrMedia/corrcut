"""Classify files dropped onto the empty editor."""

from __future__ import annotations

from pathlib import Path

from corrcut.io.project_store import PROJECT_SUFFIX

# Same set as the File → Open media dialog.
MEDIA_SUFFIXES = frozenset({".mkv", ".mp4", ".avi", ".mov", ".m4v", ".wav", ".flac"})


def classify_open_path(path: Path) -> str | None:
    """``project`` or ``media`` when *path* can be opened from a drop."""
    suffix = path.suffix.lower()
    if suffix == PROJECT_SUFFIX:
        return "project"
    if suffix in MEDIA_SUFFIXES:
        return "media"
    return None


def first_openable_path(paths: list[Path]) -> Path | None:
    for path in paths:
        if classify_open_path(path) is not None:
            return path
    return None
