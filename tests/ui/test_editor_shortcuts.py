"""Editor shortcuts: JKL shuttles playback, D toggles the selected edit."""

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QAction, QKeySequence, QShortcut
from PySide6.QtWidgets import QApplication

from corrcut.domain.dialogue_target import DialogueTargetStrategy
from corrcut.domain.operation import Operation, OperationType
from corrcut.domain.project import Project, SourceFileRecord, TrackRecord
from corrcut.domain.time_point import TimePoint
from corrcut.ui.editor_window import EditorWindow


def _mute_project() -> Project:
    op = Operation.create(
        OperationType.MUTE,
        1,
        [],
        TimePoint.from_seconds_float(2.0, 48000),
        TimePoint.from_seconds_float(5.0, 48000),
    )
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
        operations=[op],
    )


class _ShuttleBackend:
    def __init__(self) -> None:
        self.playing = False

    def is_playing(self) -> bool:
        return self.playing

    def set_playback_rate(self, rate: float) -> float:
        return float(rate)

    def play(self) -> None:
        self.playing = True

    def pause(self) -> None:
        self.playing = False

    def position(self) -> float:
        return 0.0


def _sequences(window: EditorWindow) -> list[str]:
    bound: list[str] = []
    for action in window.findChildren(QAction):
        text = action.shortcut().toString(QKeySequence.SequenceFormat.PortableText)
        if text:
            bound.append(text)
    for shortcut in window.findChildren(QShortcut):
        text = shortcut.key().toString(QKeySequence.SequenceFormat.PortableText)
        if text:
            bound.append(text)
    return bound


def _show(qtbot, window: EditorWindow) -> None:  # type: ignore[no-untyped-def]
    window.show()
    qtbot.waitExposed(window)
    window.activateWindow()
    qtbot.waitUntil(lambda: QApplication.activeWindow() is window, timeout=1000)


def test_jkl_and_d_are_bound_once(editor_window: EditorWindow) -> None:
    bound = _sequences(editor_window)
    assert bound.count("J") == 1
    assert bound.count("K") == 1
    assert bound.count("L") == 1
    assert bound.count("D") == 1
    assert "S" not in bound
    assert "Shift+J" not in bound
    assert "Shift+L" not in bound


def test_shuttle_hint_does_not_cover_the_menu_bar(qtbot, editor_window: EditorWindow) -> None:  # type: ignore[no-untyped-def]
    _show(qtbot, editor_window)
    hint = editor_window._shuttle_key_hint
    assert not hint.isVisible()
    bar = editor_window.menuBar()
    for action in bar.actions()[:2]:
        center = bar.mapToGlobal(bar.actionGeometry(action).center())
        hit = QApplication.widgetAt(center)
        assert hit is bar


def test_jkl_shuttles_and_k_does_not_arm_skip(qtbot, editor_window: EditorWindow) -> None:  # type: ignore[no-untyped-def]
    panel = editor_window._preview_panel
    panel._backend = _ShuttleBackend()  # type: ignore[assignment]
    panel.set_playback_rate(4)
    _show(qtbot, editor_window)
    play = panel._play_btn
    play.setFocus()

    qtbot.keyClick(play, Qt.Key.Key_K)
    assert panel.playback_rate() == 1
    assert editor_window._timeline._draw_operation_type is not OperationType.SKIP
    assert panel._backend.playing is False  # type: ignore[attr-defined]

    qtbot.keyClick(play, Qt.Key.Key_L)
    assert panel.playback_rate() == 1
    assert panel._backend.playing is True  # type: ignore[attr-defined]

    qtbot.keyClick(play, Qt.Key.Key_L)
    assert panel.playback_rate() == 2

    qtbot.keyClick(play, Qt.Key.Key_J)
    assert panel.playback_rate() == 1

    qtbot.keyPress(play, Qt.Key.Key_K)
    qtbot.keyClick(play, Qt.Key.Key_L)
    qtbot.keyRelease(play, Qt.Key.Key_K)
    assert panel.playback_rate() == 0.5
    assert panel._backend.playing is True  # type: ignore[attr-defined]


def test_d_toggles_selected_edit_when_preview_has_focus(qtbot, editor_window: EditorWindow) -> None:  # type: ignore[no-untyped-def]
    project = _mute_project()
    op = project.operations[0]
    editor_window._set_project(project)
    editor_window._on_timeline_region_selected(op.id)
    _show(qtbot, editor_window)
    play = editor_window._preview_panel._play_btn
    play.setFocus()

    qtbot.keyClick(play, Qt.Key.Key_D)
    assert op.enabled is False

    qtbot.keyClick(play, Qt.Key.Key_D)
    assert op.enabled is True


def test_d_toggles_once_when_timeline_has_focus(qtbot, editor_window: EditorWindow) -> None:  # type: ignore[no-untyped-def]
    project = _mute_project()
    op = project.operations[0]
    editor_window._set_project(project)
    editor_window._on_timeline_region_selected(op.id)
    _show(qtbot, editor_window)
    editor_window._timeline.setFocus()

    qtbot.keyClick(editor_window._timeline, Qt.Key.Key_D)
    assert op.enabled is False


def test_d_toggles_once_when_edits_list_has_focus(qtbot, editor_window: EditorWindow) -> None:  # type: ignore[no-untyped-def]
    project = _mute_project()
    op = project.operations[0]
    editor_window._set_project(project)
    editor_window._on_timeline_region_selected(op.id)
    _show(qtbot, editor_window)
    edits = editor_window._operations_panel._list
    edits.setFocus()

    qtbot.keyClick(edits, Qt.Key.Key_D)
    assert op.enabled is False


def test_typing_in_description_does_not_run_shortcuts(
    qtbot,
    editor_window: EditorWindow,  # type: ignore[no-untyped-def]
) -> None:
    project = _mute_project()
    op = project.operations[0]
    editor_window._set_project(project)
    editor_window._on_timeline_region_selected(op.id)
    editor_window._preview_panel.set_playback_rate(4)
    _show(qtbot, editor_window)
    description = editor_window._operations_panel._description_edit
    description.setFocus()
    description.clear()

    qtbot.keyClicks(description, "djkl")
    assert description.text() == "djkl"
    assert op.enabled is True
    assert editor_window._preview_panel.playback_rate() == 4
