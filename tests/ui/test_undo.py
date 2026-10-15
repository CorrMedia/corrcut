"""Editor undo/redo integration tests."""

from __future__ import annotations

from pathlib import Path

import pytest

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


def test_undo_redo_create_and_delete_operation(editor_window: EditorWindow) -> None:
    editor_window._set_project(_loaded_project())
    assert not editor_window._undo_stack.can_undo()

    editor_window._on_operation_created("mute", 1.0, 2.0)
    assert len(editor_window._project.operations) == 1
    assert editor_window._undo_stack.can_undo()

    editor_window._undo()
    assert not editor_window._project.operations
    assert editor_window._undo_stack.can_redo()

    editor_window._redo()
    assert len(editor_window._project.operations) == 1


def test_creating_operation_does_not_move_playhead(editor_window: EditorWindow) -> None:
    editor_window._set_project(_loaded_project())
    editor_window._timeline.set_playhead(12.0, notify=False)
    editor_window._on_operation_created("mute", 1.0, 2.0)
    assert editor_window._timeline.playhead_seconds() == 12.0


def test_apply_edit_type_changes_selected_operation(editor_window: EditorWindow) -> None:
    editor_window._set_project(_loaded_project())
    editor_window._on_operation_created("mute", 1.0, 2.0)
    op = editor_window._project.operations[0]
    editor_window._timeline.select_operation(op.id)
    editor_window._apply_edit_type(OperationType.BEEP)
    assert op.type is OperationType.BEEP
    assert editor_window._timeline._audio_draw_type is OperationType.BEEP  # noqa: SLF001


def test_undo_menu_actions_follow_stack(editor_window: EditorWindow) -> None:
    editor_window._set_project(_loaded_project())
    assert editor_window._undo_action is not None
    assert editor_window._redo_action is not None
    assert not editor_window._undo_action.isEnabled()
    assert not editor_window._redo_action.isEnabled()

    editor_window._on_operation_created("mute", 1.0, 2.0)
    assert editor_window._undo_action.isEnabled()
    assert not editor_window._redo_action.isEnabled()

    editor_window._undo()
    assert not editor_window._undo_action.isEnabled()
    assert editor_window._redo_action.isEnabled()


def test_clear_project_clears_undo_stack(editor_window: EditorWindow) -> None:
    editor_window._set_project(_loaded_project())
    editor_window._on_operation_created("mute", 1.0, 2.0)
    assert editor_window._undo_stack.can_undo()
    editor_window._set_project(None)
    assert not editor_window._undo_stack.can_undo()


def test_undo_restores_operation_timing(editor_window: EditorWindow) -> None:
    project = _loaded_project()
    op = Operation.create(
        OperationType.MUTE,
        1,
        ["L", "R"],
        TimePoint.from_seconds_float(5.0, 48000),
        TimePoint.from_seconds_float(6.0, 48000),
        order=0,
    )
    project.operations.append(op)
    editor_window._set_project(project)
    editor_window._on_operation_moved(op.id, 10.0, 12.0, final=True)
    assert float(editor_window._project.operations[0].time_range.start.seconds) == 10.0

    editor_window._undo()
    assert float(editor_window._project.operations[0].time_range.start.seconds) == 5.0


def test_undo_restores_multi_event_drag_gesture(editor_window: EditorWindow) -> None:
    """A region drag emits many non-final moves then one final move."""
    project = _loaded_project()
    op = Operation.create(
        OperationType.MUTE,
        1,
        ["L", "R"],
        TimePoint.from_seconds_float(5.0, 48000),
        TimePoint.from_seconds_float(6.0, 48000),
        order=0,
    )
    project.operations.append(op)
    editor_window._set_project(project)

    # Simulate a drag: intermediate frames are final=False, release is final=True.
    editor_window._on_operation_moved(op.id, 7.0, 8.0, final=False)
    editor_window._on_operation_moved(op.id, 9.0, 10.0, final=False)
    editor_window._on_operation_moved(op.id, 11.0, 12.0, final=True)
    assert float(editor_window._project.operations[0].time_range.start.seconds) == 11.0
    assert editor_window._undo_stack.can_undo()

    editor_window._undo()
    assert float(editor_window._project.operations[0].time_range.start.seconds) == 5.0

    editor_window._redo()
    assert float(editor_window._project.operations[0].time_range.start.seconds) == 11.0


def test_undo_channel_change(editor_window: EditorWindow) -> None:
    project = _loaded_project()
    # Stereo layouts use FL/FR labels (not L/R) when replacing checked speakers.
    op = Operation.create(
        OperationType.MUTE,
        1,
        ["FL", "FR"],
        TimePoint.from_seconds_float(5.0, 48000),
        TimePoint.from_seconds_float(6.0, 48000),
        order=0,
    )
    project.operations.append(op)
    editor_window._set_project(project)

    editor_window._on_channels_changed(op.id, ["FL"])
    assert editor_window._project.operations[0].scope.channels == ["FL"]

    editor_window._undo()
    assert editor_window._project.operations[0].scope.channels == ["FL", "FR"]


def test_region_trim_during_scrub_defers_lavfi_rebuild(
    editor_window: EditorWindow,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = _loaded_project()
    op = Operation.create(
        OperationType.MUTE,
        1,
        ["L", "R"],
        TimePoint.from_seconds_float(5.0, 48000),
        TimePoint.from_seconds_float(6.0, 48000),
        order=0,
    )
    project.operations.append(op)
    editor_window._set_project(project)

    syncs: list[int] = []
    monkeypatch.setattr(
        editor_window._preview_panel,
        "sync_track_and_filters",
        lambda **_kwargs: syncs.append(1),
    )

    editor_window._timeline_scrub_active = True
    editor_window._on_operation_moved(op.id, 5.0, 8.0, final=True)
    assert syncs == []
    assert editor_window._pending_preview_sync_after_scrub

    editor_window._release_scrub_lock()
    assert syncs == [1]
    assert not editor_window._pending_preview_sync_after_scrub
    assert not editor_window._timeline_scrub_active
