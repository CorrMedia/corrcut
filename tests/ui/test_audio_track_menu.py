"""Timeline audio menu shows the resolved language for the active track."""

from corrcut.domain.dialogue_target import DialogueTargetStrategy
from corrcut.domain.operation import Operation, OperationType
from corrcut.domain.project import Project, TrackRecord
from corrcut.domain.time_point import TimePoint
from corrcut.ui.editor_window import EditorWindow


def test_timeline_audio_menu_labels_language_and_shares_edits(editor_window: EditorWindow, qtbot) -> None:  # type: ignore[no-untyped-def]
    surround = TrackRecord(
        stream_index=1,
        codec="truehd",
        channel_layout="7.1",
        channels=8,
        sample_rate=48000,
        language="eng",
        dialogue_target_strategy=DialogueTargetStrategy.CENTER_CHANNEL,
        title="Surround",
    )
    stereo = TrackRecord(
        stream_index=2,
        codec="aac",
        channel_layout="stereo",
        channels=2,
        sample_rate=48000,
        language=None,
        dialogue_target_strategy=DialogueTargetStrategy.FULL_MIX,
        title="Commentary",
    )
    mute = Operation.create(
        OperationType.MUTE,
        1,
        ["FC"],
        TimePoint.from_seconds_float(1.0, 48000),
        TimePoint.from_seconds_float(2.0, 48000),
        language="eng",
    )
    editor_window._set_project(
        Project(tracks=[surround, stereo], operations=[mute], selected_audio_stream_index=1)
    )
    qtbot.waitUntil(lambda: editor_window._timeline_panel._audio_track_btn.text().startswith("English"))
    button = editor_window._timeline_panel._audio_track_btn
    assert button.text() == "English · 7.1"
    labels = [action.text() for action in button.menu().actions() if action.text()]
    assert any(label.startswith("English · 7.1") and "1 edit" in label for label in labels)
    assert any("English (assumed)" in label and "1 edit" in label for label in labels)

    editor_window._on_track_selected(2)
    qtbot.waitUntil(lambda: "assumed" in editor_window._timeline_panel._audio_track_btn.text())
    assert len(editor_window._project.timeline_operations()) == 1
