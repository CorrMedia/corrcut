from pathlib import Path
from unittest.mock import MagicMock

from PySide6.QtCore import QEvent, Qt
from PySide6.QtGui import QKeyEvent, QKeySequence, QShortcut

from corrcut.domain.dialogue_target import DialogueTargetStrategy
from corrcut.domain.project import Project, SourceFileRecord, TrackRecord
from corrcut.ui.editor.flags import snap_flag_seconds
from corrcut.ui.editor_window import EditorWindow
from corrcut.ui.preview_panel import PreviewPanel, _PreviewFullscreenWindow


def _loaded_project() -> Project:
    return Project(
        source=SourceFileRecord(
            path=Path(__file__).with_name("missing_editor_fixture.mkv"),
            fingerprint={"test": True},
            probe={"duration_seconds": 120.0, "subtitle_streams": []},
        ),
        tracks=[
            TrackRecord(
                stream_index=1,
                codec="aac",
                channel_layout="stereo",
                channels=2,
                sample_rate=48000,
                language="eng",
                dialogue_target_strategy=DialogueTargetStrategy.FULL_MIX,
            ),
        ],
        selected_audio_stream_index=1,
    )


def test_drop_flag_at_playhead_is_undoable(editor_window: EditorWindow) -> None:
    editor_window._set_project(_loaded_project())
    editor_window._timeline.set_playhead(12.0)
    editor_window._drop_flag(1)
    assert len(editor_window._project.timeline_flags) == 1
    assert editor_window._project.timeline_flags[0].slot == 1
    assert abs(editor_window._project.timeline_flags[0].seconds - 12.0) < 0.001
    assert editor_window._timeline.selected_flag_id() == editor_window._project.timeline_flags[0].id

    editor_window._undo()
    assert not editor_window._project.timeline_flags
    editor_window._redo()
    assert len(editor_window._project.timeline_flags) == 1


def test_delete_flag_is_undoable(editor_window: EditorWindow) -> None:
    editor_window._set_project(_loaded_project())
    editor_window._timeline.set_playhead(8.0)
    editor_window._drop_flag(2)
    flag_id = editor_window._project.timeline_flags[0].id
    editor_window._delete_flag(flag_id)
    assert not editor_window._project.timeline_flags
    editor_window._undo()
    assert len(editor_window._project.timeline_flags) == 1
    assert editor_window._project.timeline_flags[0].id == flag_id


def test_numpad_shortcut_drops_flag(editor_window: EditorWindow) -> None:
    editor_window._set_project(_loaded_project())
    editor_window._timeline.set_playhead(3.0)
    target = QKeySequence("Num+1")
    for shortcut in editor_window.findChildren(QShortcut):
        if shortcut.key() == target:
            shortcut.activated.emit()
            break
    else:
        raise AssertionError("numpad 1 ApplicationShortcut was not registered")
    assert len(editor_window._project.timeline_flags) == 1
    assert editor_window._project.timeline_flags[0].slot == 1


def test_fullscreen_numpad_emits_flag_requested() -> None:
    panel = PreviewPanel()
    slots: list[int] = []
    panel.flag_requested.connect(slots.append)
    window = _PreviewFullscreenWindow(panel)
    window.keyPressEvent(
        QKeyEvent(
            QKeyEvent.Type.KeyPress,
            Qt.Key.Key_2,
            Qt.KeyboardModifier.KeypadModifier,
        )
    )
    window.keyPressEvent(
        QKeyEvent(
            QKeyEvent.Type.KeyPress,
            Qt.Key.Key_3,
            Qt.KeyboardModifier.NoModifier,
        )
    )
    assert slots == [2, 3]


def test_fullscreen_does_not_register_duplicate_flag_shortcuts(
    editor_window: EditorWindow,
) -> None:
    window = _PreviewFullscreenWindow(editor_window._preview_panel)
    target = QKeySequence("Num+1")
    editor_matches = [
        shortcut
        for shortcut in editor_window.findChildren(QShortcut)
        if shortcut.key() == target
    ]
    fullscreen_matches = [
        shortcut for shortcut in window.findChildren(QShortcut) if shortcut.key() == target
    ]
    assert len(editor_matches) == 1
    assert fullscreen_matches == []


def test_fullscreen_event_filter_emits_flag_requested() -> None:
    panel = PreviewPanel()
    slots: list[int] = []
    panel.flag_requested.connect(slots.append)
    window = _PreviewFullscreenWindow(panel)
    window.show()
    event = QKeyEvent(
        QEvent.Type.KeyPress,
        Qt.Key.Key_1,
        Qt.KeyboardModifier.KeypadModifier,
    )
    try:
        assert window.eventFilter(window, event) is True
        assert slots == [1]
    finally:
        window.hide()
        window.restore_cursor()


def test_drop_flag_uses_playhead_when_playback_clock_is_far(
    editor_window: EditorWindow,
) -> None:
    editor_window._set_project(_loaded_project())
    editor_window._timeline.set_playhead(4.0)
    editor_window._preview_playing = True
    editor_window._preview_panel._backend = MagicMock()
    editor_window._preview_panel._backend.position.return_value = 18.5
    editor_window._drop_flag(1)
    assert abs(editor_window._project.timeline_flags[0].seconds - 4.0) < 0.001
    assert abs(editor_window._timeline.playhead_seconds() - 4.0) < 0.001


def test_drop_flag_snaps_to_shown_frame_when_paused(editor_window: EditorWindow) -> None:
    editor_window._set_project(_loaded_project())
    editor_window._preview_playing = False
    editor_window._timeline.set_playhead(12.0)
    editor_window._preview_panel._backend = MagicMock()
    editor_window._preview_panel._backend.position.return_value = 12.04
    editor_window._drop_flag(1)
    assert abs(editor_window._project.timeline_flags[0].seconds - 12.04) < 0.001
    assert abs(editor_window._timeline.playhead_seconds() - 12.04) < 0.001


def test_snap_flag_seconds_rejects_stale_media_clock() -> None:
    assert snap_flag_seconds(4.0, 18.5) == 4.0
    assert snap_flag_seconds(4.0, 0.0) == 4.0
    assert abs(snap_flag_seconds(12.0, 12.04) - 12.04) < 1e-9
    assert snap_flag_seconds(12.0, None) == 12.0


def test_flag_context_menu_changes_color(editor_window: EditorWindow) -> None:
    editor_window._set_project(_loaded_project())
    editor_window._timeline.set_playhead(12.0)
    editor_window._drop_flag(1)
    flag = editor_window._project.timeline_flags[0]

    menu = editor_window._flag_context_menu(flag.id)
    texts = [action.text() for action in menu.actions()]
    assert "Custom color…" in texts
    assert "Delete flag" in texts
    assert "Use slot color" not in texts
    colors = [action for action in menu.actions() if action.isCheckable()]
    assert len(colors) == 3
    assert colors[0].isChecked()
    assert not colors[1].isChecked()

    colors[1].trigger()
    assert flag.slot == 2
    assert flag.color is None
    assert editor_window._timeline._flags[flag.id].slot() == 2
    editor_window._undo()
    assert flag.slot == 1
    editor_window._redo()
    assert flag.slot == 2

    editor_window._set_flag_color(flag.id, "#00aa00")
    assert flag.color == "#00aa00"
    assert editor_window._timeline._flags[flag.id].color().name() == "#00aa00"
    custom_menu = editor_window._flag_context_menu(flag.id)
    assert not any(action.isChecked() for action in custom_menu.actions() if action.isCheckable())
    use_slot = next(action for action in custom_menu.actions() if action.text() == "Use slot color")
    use_slot.trigger()
    assert flag.color is None
    assert flag.slot == 2
    editor_window._undo()
    assert flag.color == "#00aa00"
    editor_window._redo()
    assert flag.color is None

    delete = next(action for action in menu.actions() if action.text() == "Delete flag")
    delete.trigger()
    assert editor_window._project.timeline_flags == []


def test_move_flag_is_undoable(editor_window: EditorWindow) -> None:
    editor_window._set_project(_loaded_project())
    editor_window._timeline.set_playhead(12.0)
    editor_window._drop_flag(1)
    flag_id = editor_window._project.timeline_flags[0].id
    editor_window._on_flag_moved(flag_id, 12.0, False)
    editor_window._on_flag_moved(flag_id, 20.0, True)
    assert abs(editor_window._project.timeline_flags[0].seconds - 20.0) < 0.001
    editor_window._undo()
    assert abs(editor_window._project.timeline_flags[0].seconds - 12.0) < 0.001
    editor_window._redo()
    assert abs(editor_window._project.timeline_flags[0].seconds - 20.0) < 0.001
