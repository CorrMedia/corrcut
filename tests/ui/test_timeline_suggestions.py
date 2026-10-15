"""Timeline AI suggestion placeholder sync."""

from __future__ import annotations

from fractions import Fraction
from pathlib import Path

from PySide6.QtCore import QEvent, QPointF, Qt
from PySide6.QtGui import QKeyEvent, QMouseEvent
from PySide6.QtWidgets import QApplication

from corrcut.domain.ai_suggestion import AiSuggestion, SuggestionStatus
from corrcut.domain.dialogue_target import DialogueTargetStrategy
from corrcut.domain.operation import Operation, OperationType, TimeRange
from corrcut.domain.project import Project, SourceFileRecord, TrackRecord
from corrcut.domain.time_point import TimePoint
from corrcut.ui.editor_window import EditorWindow
from corrcut.ui.timeline.suggestion_placeholder_item import (
    SuggestionPlaceholderItem,
    suggestion_edit_label,
)
from corrcut.ui.timeline.timeline_lanes import LANE_HEIGHT, RULER_HEIGHT
from corrcut.ui.timeline.timeline_widget import TimelineWidget

_app = QApplication.instance() or QApplication([])


def _suggestion(start: float, end: float) -> AiSuggestion:
    return AiSuggestion.create(
        stream_index=0,
        time_range=TimeRange(
            start=TimePoint(Fraction(start)),
            end=TimePoint(Fraction(end)),
        ),
        matched_word="test",
        matched_term="test",
        transcript_phrase="test phrase",
        confidence=0.9,
        whisper_probability=0.9,
        alignment_quality="good",
    )


def test_sync_suggestions_adds_and_removes_placeholders() -> None:
    timeline = TimelineWidget()
    timeline.set_duration(60.0)

    first = _suggestion(1.0, 2.0)
    second = _suggestion(5.0, 6.0)
    timeline.sync_suggestions([first, second])
    assert set(timeline._suggestion_placeholders.keys()) == {first.id, second.id}  # noqa: SLF001

    timeline.sync_suggestions([second])
    assert set(timeline._suggestion_placeholders.keys()) == {second.id}  # noqa: SLF001

    timeline.sync_suggestions([])
    assert timeline._suggestion_placeholders == {}  # noqa: SLF001


def test_suggestion_edit_label_names_the_mute() -> None:
    suggestion = _suggestion(1.0, 2.0)
    assert suggestion_edit_label(suggestion) == "Mute · test"

    suggestion.matched_word = ""
    suggestion.matched_term = "hell"
    suggestion.category = "profanity"
    assert suggestion_edit_label(suggestion) == "Mute · h*ll"

    suggestion.matched_term = ""
    suggestion.category = "profanity"
    assert suggestion_edit_label(suggestion) == "Mute · profanity"

    suggestion.category = ""
    assert suggestion_edit_label(suggestion) == "Mute"

    suggestion.matched_word = "damn."
    suggestion.matched_term = "damn"
    suggestion.category = "profanity"
    assert suggestion_edit_label(suggestion) == "Mute · d*mn"

    suggestion.matched_word = "son of a bitch,"
    suggestion.matched_term = "bitch"
    assert suggestion_edit_label(suggestion) == "Mute · son of a b*tch"

    suggestion.matched_word = "well-known"
    suggestion.matched_term = "well-known"
    suggestion.category = ""
    assert suggestion_edit_label(suggestion) == "Mute · well-known"

    suggestion.matched_word = "fuckin'"
    suggestion.matched_term = "fuckin'"
    assert suggestion_edit_label(suggestion) == "Mute · f*ck"


def test_sync_suggestions_updates_label() -> None:
    timeline = TimelineWidget()
    timeline.set_duration(60.0)
    suggestion = _suggestion(1.0, 2.0)
    timeline.sync_suggestions([suggestion])
    item = timeline._suggestion_placeholders[suggestion.id]  # noqa: SLF001
    assert item.label() == "Mute · test"

    suggestion.matched_word = "damn"
    suggestion.category = "profanity"
    timeline.sync_suggestions([suggestion])
    assert item.label() == "Mute · d*mn"
    assert "Enter accepts" in item.toolTip()


def _shown_timeline() -> TimelineWidget:
    timeline = TimelineWidget()
    timeline.resize(800, timeline.height_for_lanes(LANE_HEIGHT))
    timeline.show()
    timeline.set_duration(60.0)
    timeline.set_pixels_per_second(80.0)
    _app.processEvents()
    return timeline


def _press(widget: TimelineWidget, view_pos: QPointF, *, button: Qt.MouseButton) -> None:
    event = QMouseEvent(
        QEvent.Type.MouseButtonPress,
        view_pos,
        view_pos,
        QPointF(),
        button,
        button,
        Qt.KeyboardModifier.NoModifier,
    )
    widget.mousePressEvent(event)


def _item_view_pos(widget: TimelineWidget, item: SuggestionPlaceholderItem) -> QPointF:
    scene_pos = item.mapToScene(item.rect().center())
    return QPointF(widget.mapFromScene(scene_pos))


def test_clicking_suggestion_selects_without_seeking() -> None:
    timeline = _shown_timeline()
    timeline.set_playhead(0.0)
    suggestion = _suggestion(2.0, 4.0)
    timeline.sync_suggestions([suggestion])
    item = timeline._suggestion_placeholders[suggestion.id]  # noqa: SLF001
    _press(timeline, _item_view_pos(timeline, item), button=Qt.MouseButton.LeftButton)
    assert timeline.selected_suggestion_id() == suggestion.id
    assert timeline.playhead_seconds() == 0.0

    timeline.mousePressEvent(
        QMouseEvent(
            QEvent.Type.MouseButtonPress,
            QPointF(12.0, RULER_HEIGHT / 2.0),
            QPointF(12.0, RULER_HEIGHT / 2.0),
            QPointF(),
            Qt.MouseButton.LeftButton,
            Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.NoModifier,
        )
    )
    assert timeline.selected_suggestion_id() is None


def test_delete_rejects_and_enter_accepts_selected_suggestion() -> None:
    timeline = _shown_timeline()
    suggestion = _suggestion(2.0, 4.0)
    timeline.sync_suggestions([suggestion])
    timeline.select_suggestion(suggestion.id)
    rejected: list[str] = []
    accepted: list[str] = []
    timeline.suggestion_reject_requested.connect(rejected.append)
    timeline.suggestion_accept_requested.connect(accepted.append)

    timeline.keyPressEvent(
        QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Delete, Qt.KeyboardModifier.NoModifier)
    )
    assert rejected == [suggestion.id]
    assert accepted == []

    timeline.keyPressEvent(
        QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Return, Qt.KeyboardModifier.NoModifier)
    )
    timeline.keyPressEvent(
        QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Enter, Qt.KeyboardModifier.KeypadModifier)
    )
    assert accepted == [suggestion.id, suggestion.id]


def test_delete_shortcut_rejects_selected_suggestion(editor_window: EditorWindow) -> None:
    suggestion = _suggestion(2.0, 4.0)
    suggestion.stream_index = 1
    editor_window._set_project(
        Project(
            source=SourceFileRecord(
                path=Path(__file__).with_name("missing_editor_fixture.mkv"),
                fingerprint={"test": True},
                probe={"duration_seconds": 60.0, "subtitle_streams": []},
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
            ai_suggestions=[suggestion],
        )
    )
    editor_window._timeline.select_suggestion(suggestion.id)
    editor_window._delete_selected_operation()
    assert suggestion.status == SuggestionStatus.REJECTED
    assert suggestion.id not in editor_window._timeline._suggestion_placeholders  # noqa: SLF001


def test_region_click_wins_over_overlapping_suggestion() -> None:
    timeline = _shown_timeline()
    suggestion = _suggestion(2.0, 5.0)
    operation = Operation.create(
        OperationType.MUTE,
        0,
        ["FC"],
        TimePoint(Fraction(2)),
        TimePoint(Fraction(5)),
    )
    timeline.sync_operations([operation])
    timeline.sync_suggestions([suggestion])
    item = timeline._suggestion_placeholders[suggestion.id]  # noqa: SLF001
    _press(timeline, _item_view_pos(timeline, item), button=Qt.MouseButton.LeftButton)
    assert timeline.selected_operation_id() == operation.id
    assert timeline.selected_suggestion_id() is None


def test_suggestion_placeholder_geometry_tracks_scroll() -> None:
    item = SuggestionPlaceholderItem(
        "s1",
        10.0,
        12.0,
        pixels_per_second=100.0,
        track_height=120.0,
        scroll_x=500.0,
        duration_s=60.0,
    )
    assert item.rect().left() == 500.0
    # Suggestions live on the audio lane (below the ruler + video band).
    from corrcut.ui.timeline.timeline_lanes import AUDIO_TOP

    assert item.rect().top() >= AUDIO_TOP
    item.set_scroll_x(0.0)
    assert item.rect().left() == 1000.0
