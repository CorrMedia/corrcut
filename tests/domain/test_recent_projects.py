"""Recent projects persistence."""

from __future__ import annotations

from pathlib import Path

import pytest
from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication

from corrcut.io.recent_projects import (
    _KEY_RECENT_PATHS,
    _SETTINGS_APP,
    _SETTINGS_ORG,
    add_recent_project,
    list_recent_projects,
    remove_recent_project,
)

_app = QApplication.instance() or QApplication([])


@pytest.fixture(autouse=True)
def _clear_recent_settings() -> None:
    settings = QSettings(_SETTINGS_ORG, _SETTINGS_APP)
    settings.remove(_KEY_RECENT_PATHS)


def test_recent_projects_round_trip(tmp_path: Path) -> None:
    first = tmp_path / "alpha.corrcut"
    second = tmp_path / "beta.corrcut"
    first.touch()
    second.touch()

    add_recent_project(first)
    add_recent_project(second)
    add_recent_project(first)

    entries = list_recent_projects()
    assert [entry.path for entry in entries] == [first.resolve(), second.resolve()]


def test_remove_recent_project(tmp_path: Path) -> None:
    path = tmp_path / "gone.corrcut"
    path.touch()
    add_recent_project(path)
    remove_recent_project(path)
    assert list_recent_projects() == []
