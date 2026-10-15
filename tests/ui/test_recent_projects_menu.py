"""File → Open recent lists previously opened projects."""

from __future__ import annotations

from pathlib import Path

import pytest
from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QMessageBox

from corrcut.io.recent_projects import (
    _KEY_RECENT_PATHS,
    _SETTINGS_APP,
    _SETTINGS_ORG,
    add_recent_project,
    list_recent_projects,
)
from corrcut.ui.editor_window import EditorWindow


@pytest.fixture
def _clear_recent_settings() -> None:
    settings = QSettings(_SETTINGS_ORG, _SETTINGS_APP)
    settings.remove(_KEY_RECENT_PATHS)
    yield
    settings.remove(_KEY_RECENT_PATHS)


def _menu_labels(window: EditorWindow) -> list[str]:
    window._populate_recent_projects_menu()
    return [action.text() for action in window._recent_projects_menu.actions()]


def test_recent_menu_is_empty_until_a_project_is_opened(
    editor_window: EditorWindow, _clear_recent_settings: None
) -> None:
    labels = _menu_labels(editor_window)
    assert labels == ["No recent projects"]
    assert editor_window._recent_projects_menu.actions()[0].isEnabled() is False


def test_recent_menu_opens_the_chosen_project(
    editor_window: EditorWindow,
    tmp_path: Path,
    _clear_recent_settings: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = tmp_path / "Night & Watch.corrcut"
    project.touch()
    add_recent_project(project)
    opened: list[Path] = []
    monkeypatch.setattr(editor_window, "_open_recent_project", opened.append)

    editor_window._populate_recent_projects_menu()
    actions = editor_window._recent_projects_menu.actions()
    assert [action.text() for action in actions] == ["Night && Watch"]
    assert actions[0].toolTip() == str(project.resolve())
    actions[0].trigger()
    assert opened == [project.resolve()]


def test_missing_recent_project_can_be_removed(
    editor_window: EditorWindow,
    tmp_path: Path,
    _clear_recent_settings: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    missing = tmp_path / "Gone.corrcut"
    add_recent_project(missing)
    monkeypatch.setattr(
        "corrcut.ui.editor.project_session.QMessageBox.warning",
        lambda *_args, **_kwargs: QMessageBox.StandardButton.Yes,
    )
    loaded: list[Path] = []
    monkeypatch.setattr(editor_window, "load_project_file", loaded.append)

    assert _menu_labels(editor_window) == ["Gone (missing)"]
    editor_window._open_recent_project_now(missing.resolve())
    assert loaded == []
    assert list_recent_projects() == []
