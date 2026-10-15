"""Unsaved project change detection."""

from __future__ import annotations

from pathlib import Path

import pytest
from PySide6.QtWidgets import QMessageBox

from corrcut.domain.dialogue_target import DialogueTargetStrategy
from corrcut.domain.operation import Operation, OperationType
from corrcut.domain.project import Project, SourceFileRecord, TrackRecord
from corrcut.domain.time_point import TimePoint
from corrcut.ui.editor_window import EditorWindow


def _loaded_project() -> Project:
    tracks = [
        TrackRecord(
            stream_index=1,
            codec="aac",
            channel_layout="stereo",
            channels=2,
            sample_rate=48000,
            language="eng",
            dialogue_target_strategy=DialogueTargetStrategy.FULL_MIX,
        ),
    ]
    # Missing media path — avoids mpv/waveform background jobs in editor tests.
    return Project(
        source=SourceFileRecord(
            path=Path(__file__).with_name("missing_editor_fixture.mkv"),
            fingerprint={"test": True},
            probe={"duration_seconds": 120.0, "subtitle_streams": []},
        ),
        tracks=tracks,
        selected_audio_stream_index=1,
        operations=[],
    )


def test_clean_project_is_not_dirty(editor_window: EditorWindow) -> None:
    editor_window._set_project(_loaded_project())
    assert editor_window._is_project_dirty() is False


def test_operation_edit_marks_project_dirty(editor_window: EditorWindow) -> None:
    project = _loaded_project()
    editor_window._set_project(project)
    project.operations.append(
        Operation.create(
            OperationType.MUTE,
            1,
            ["L", "R"],
            TimePoint.from_seconds_float(1.0, 48000),
            TimePoint.from_seconds_float(2.0, 48000),
            order=0,
        )
    )
    editor_window._notify_project_modified()
    assert editor_window._is_project_dirty() is True
    assert editor_window.windowTitle().endswith(" *")


def test_undo_back_to_saved_baseline_clears_dirty_flag(editor_window: EditorWindow) -> None:
    project = _loaded_project()
    editor_window._set_project(project)
    editor_window._on_operation_created("mute", 1.0, 2.0)
    assert editor_window._is_project_dirty() is True
    assert editor_window.windowTitle().endswith(" *")

    editor_window._undo()
    assert editor_window._is_project_dirty() is False
    assert not editor_window.windowTitle().endswith(" *")


def test_mark_project_saved_clears_dirty_flag(editor_window: EditorWindow) -> None:
    project = _loaded_project()
    editor_window._set_project(project)
    project.operations.append(
        Operation.create(
            OperationType.MUTE,
            1,
            ["L", "R"],
            TimePoint.from_seconds_float(1.0, 48000),
            TimePoint.from_seconds_float(2.0, 48000),
            order=0,
        )
    )
    editor_window._notify_project_modified()
    assert editor_window._is_project_dirty() is True
    assert editor_window.windowTitle().endswith(" *")
    editor_window._mark_project_saved()
    assert editor_window._is_project_dirty() is False
    assert not editor_window.windowTitle().endswith(" *")


def test_report_project_saved_shows_status_message(
    editor_window: EditorWindow,
) -> None:
    project = _loaded_project()
    project.project_path = Path("fixture.corrcut")
    editor_window._set_project(project)
    editor_window._report_project_saved(project.project_path)
    assert editor_window.statusBar().currentMessage() == "Saved fixture.corrcut"
    assert not editor_window.windowTitle().endswith(" *")


def test_close_project_skips_prompt_when_clean(
    editor_window: EditorWindow,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    editor_window._set_project(_loaded_project())
    assert editor_window._is_project_dirty() is False
    prompts = {"count": 0}

    def _unexpected_warning(*_args: object, **_kwargs: object) -> QMessageBox.StandardButton:
        prompts["count"] += 1
        return QMessageBox.StandardButton.Cancel

    monkeypatch.setattr(
        "corrcut.ui.editor.project_session.QMessageBox.warning",
        _unexpected_warning,
    )
    editor_window._close_project()
    assert prompts["count"] == 0
    assert editor_window._project is None


def test_close_project_prompts_when_dirty(
    editor_window: EditorWindow,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = _loaded_project()
    editor_window._set_project(project)
    project.operations.append(
        Operation.create(
            OperationType.MUTE,
            1,
            ["L", "R"],
            TimePoint.from_seconds_float(1.0, 48000),
            TimePoint.from_seconds_float(2.0, 48000),
            order=0,
        )
    )
    editor_window._notify_project_modified()
    prompts = {"count": 0}

    def _cancel_warning(*_args: object, **_kwargs: object) -> QMessageBox.StandardButton:
        prompts["count"] += 1
        return QMessageBox.StandardButton.Cancel

    monkeypatch.setattr(
        "corrcut.ui.editor.project_session.QMessageBox.warning",
        _cancel_warning,
    )
    editor_window._close_project()
    assert prompts["count"] == 1
    assert editor_window._project is not None
