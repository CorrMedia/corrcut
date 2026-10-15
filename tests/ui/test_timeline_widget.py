"""Timeline widget command/mode tests (no GUI)."""

from fractions import Fraction
from pathlib import Path

import pytest
from PySide6.QtCore import QEvent, QPointF, Qt
from PySide6.QtGui import QMouseEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QFrame

from corrcut.domain.dialogue_target import DialogueTargetStrategy
from corrcut.domain.operation import Operation, OperationType
from corrcut.domain.project import Project, SourceFileRecord, TrackRecord
from corrcut.domain.time_point import TimePoint
from corrcut.engine.audition import AuditionSpec, audition_job_spec
from corrcut.ui.timeline.timeline_lanes import (
    LANE_HEIGHT,
    MAX_LANE_HEIGHT,
    MIN_LANE_HEIGHT,
    RULER_HEIGHT,
    SCENE_HEIGHT,
    audio_lane_mid_y,
    video_lane_mid_y,
)
from corrcut.ui.timeline.timeline_mode import TimelineMode
from corrcut.ui.timeline.timeline_widget import (
    TimelineWidget,
    playhead_draw_x,
    playhead_head_polygon,
)

_app = QApplication.instance() or QApplication([])


def _center_project(ops: list[Operation] | None = None) -> Project:
    track = TrackRecord(
        stream_index=0,
        codec="aac",
        channel_layout="5.1(side)",
        channels=6,
        sample_rate=48000,
        language=None,
        dialogue_target_strategy=DialogueTargetStrategy.CENTER_CHANNEL,
    )
    return Project(
        source=SourceFileRecord(path=Path("dummy.mkv"), fingerprint={}, probe={}),
        tracks=[track],
        operations=ops or [],
        selected_audio_stream_index=0,
    )


def test_audition_job_input_seek_before_input() -> None:
    op = Operation.create(
        OperationType.MUTE,
        0,
        ["FC"],
        TimePoint(Fraction(1)),
        TimePoint(Fraction(2)),
    )
    job = audition_job_spec(
        _center_project([op]),
        AuditionSpec(output_wav=Path("out.wav"), start_seconds=Fraction(3, 2)),
    )
    assert job is not None
    assert job.command.index("-ss") < job.command.index("-i")


def test_timeline_mode_create_operation_type() -> None:
    assert TimelineMode.SELECT.create_operation_type is None
    assert TimelineMode.CREATE_MUTE.create_operation_type == OperationType.MUTE
    assert TimelineMode.CREATE_VOLUME.create_operation_type == OperationType.VOLUME
    assert TimelineMode.CREATE_BEEP.create_operation_type == OperationType.BEEP
    assert TimelineMode.CREATE_SKIP.create_operation_type == OperationType.SKIP


def _mute_op(start_s: float, end_s: float) -> Operation:
    return Operation.create(
        OperationType.MUTE,
        0,
        ["FC"],
        TimePoint.from_seconds_float(start_s, 48000),
        TimePoint.from_seconds_float(end_s, 48000),
    )


def _cover_op(start_s: float, end_s: float) -> Operation:
    return Operation.create(
        OperationType.COVER,
        -1,
        [],
        TimePoint.from_seconds_float(start_s, 48000),
        TimePoint.from_seconds_float(end_s, 48000),
    )


def _shown_timeline(ops: Operation | list[Operation]) -> TimelineWidget:
    if isinstance(ops, Operation):
        ops = [ops]
    timeline = TimelineWidget()
    timeline.resize(640, timeline.height_for_lanes(LANE_HEIGHT))
    timeline.show()
    timeline.set_duration(20.0)
    timeline.set_pixels_per_second(80.0)
    timeline.sync_operations(ops)
    _app.processEvents()
    return timeline


def _viewport_pos(
    timeline: TimelineWidget,
    time_s: float,
    *,
    x_nudge: float = 0.0,
    scene_y: float | None = None,
) -> QPointF:
    y = audio_lane_mid_y() if scene_y is None else scene_y
    return timeline.mapFromScene(QPointF(timeline._time_to_scene_x(time_s) + x_nudge, y))


def _view_mouse(
    event_type: QEvent.Type,
    pos: QPointF,
    *,
    button: Qt.MouseButton = Qt.MouseButton.LeftButton,
    buttons: Qt.MouseButton = Qt.MouseButton.LeftButton,
) -> QMouseEvent:
    return QMouseEvent(
        event_type,
        pos,
        pos,
        QPointF(),
        button,
        buttons,
        Qt.KeyboardModifier.NoModifier,
    )


def test_disabled_operation_stays_on_timeline_at_lower_opacity() -> None:
    op = _mute_op(2.0, 5.0)
    timeline = _shown_timeline(op)
    enabled_alpha = timeline._regions[op.id].brush().color().alpha()
    op.enabled = False
    timeline.sync_operations([op])
    item = timeline._regions[op.id]
    assert item.brush().color().alpha() < enabled_alpha
    assert item.pen().style() == Qt.PenStyle.DashLine


def test_keyboard_nudge_slides_selected_region() -> None:
    from PySide6.QtGui import QKeyEvent

    op = _mute_op(2.0, 5.0)
    timeline = _shown_timeline(op)
    timeline.select_operation(op.id)
    timeline.keyPressEvent(
        QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Right, Qt.KeyboardModifier.NoModifier)
    )
    item = timeline._regions[op.id]
    assert item.start_seconds() > 2.0
    assert item.end_seconds() - item.start_seconds() == pytest.approx(3.0)
    timeline.keyPressEvent(
        QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Left, Qt.KeyboardModifier.NoModifier)
    )
    assert item.start_seconds() == pytest.approx(2.0)
    assert item.end_seconds() == pytest.approx(5.0)


def test_nudge_selected_region_returns_false_without_selection() -> None:
    timeline = _shown_timeline(_mute_op(2.0, 5.0))
    assert timeline.nudge_selected_region(1) is False
    item = next(iter(timeline._regions.values()))
    assert item.start_seconds() == 2.0


def test_disabled_region_ignores_keyboard_nudge() -> None:
    from PySide6.QtGui import QKeyEvent

    op = _mute_op(2.0, 5.0)
    op.enabled = False
    timeline = _shown_timeline(op)
    timeline.select_operation(op.id)
    timeline.keyPressEvent(
        QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Right, Qt.KeyboardModifier.NoModifier)
    )
    item = timeline._regions[op.id]
    assert item.start_seconds() == 2.0
    assert item.end_seconds() == 5.0


def test_d_toggles_enabled_on_selected_edit() -> None:
    from PySide6.QtGui import QKeyEvent

    op = _mute_op(2.0, 5.0)
    timeline = _shown_timeline(op)
    timeline.select_operation(op.id)
    toggled: list[str] = []
    timeline.operation_toggle_enabled_requested.connect(toggled.append)
    timeline.keyPressEvent(
        QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_D, Qt.KeyboardModifier.NoModifier)
    )
    assert toggled == [op.id]
    assert timeline.timeline_mode() is not TimelineMode.CREATE_VOLUME


def test_d_without_selection_does_not_enter_volume_draw() -> None:
    from PySide6.QtGui import QKeyEvent

    op = _mute_op(2.0, 5.0)
    timeline = _shown_timeline(op)
    toggled: list[str] = []
    timeline.operation_toggle_enabled_requested.connect(toggled.append)
    timeline.keyPressEvent(
        QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_D, Qt.KeyboardModifier.NoModifier)
    )
    assert toggled == []
    assert timeline.timeline_mode() is TimelineMode.SELECT


def test_trimming_right_handle_previews_out_without_moving_playhead() -> None:
    op = _mute_op(2.0, 5.0)
    timeline = _shown_timeline(op)
    timeline.set_playhead(1.0, notify=False)
    playheads: list[float] = []
    previews: list[float] = []
    finished: list[float] = []
    timeline.playhead_changed.connect(playheads.append)
    timeline.trim_preview_seek.connect(previews.append)
    timeline.trim_preview_finished.connect(finished.append)

    press_pos = _viewport_pos(timeline, 5.0, x_nudge=-2.0)
    timeline.mousePressEvent(_view_mouse(QEvent.Type.MouseButtonPress, press_pos))
    assert timeline.playhead_seconds() == 1.0
    assert playheads == []
    assert previews[-1] == 5.0

    drag_pos = _viewport_pos(timeline, 7.0)
    timeline.mouseMoveEvent(
        _view_mouse(QEvent.Type.MouseMove, drag_pos, buttons=Qt.MouseButton.LeftButton)
    )
    out_s = timeline._regions[op.id].end_seconds()
    assert out_s > 5.0
    assert timeline.playhead_seconds() == 1.0
    assert playheads == []
    assert previews[-1] == out_s

    timeline.mouseReleaseEvent(
        _view_mouse(QEvent.Type.MouseButtonRelease, drag_pos, buttons=Qt.MouseButton.NoButton)
    )
    assert timeline.playhead_seconds() == 1.0
    assert playheads == []
    assert finished == [out_s]


def test_trimming_left_handle_previews_in_without_moving_playhead() -> None:
    op = _mute_op(2.0, 5.0)
    timeline = _shown_timeline(op)
    timeline.set_playhead(4.0, notify=False)
    previews: list[float] = []
    timeline.trim_preview_seek.connect(previews.append)
    press_pos = _viewport_pos(timeline, 2.0, x_nudge=2.0)
    timeline.mousePressEvent(_view_mouse(QEvent.Type.MouseButtonPress, press_pos))
    assert timeline.playhead_seconds() == 4.0
    assert previews[-1] == 2.0

    drag_pos = _viewport_pos(timeline, 3.0)
    timeline.mouseMoveEvent(
        _view_mouse(QEvent.Type.MouseMove, drag_pos, buttons=Qt.MouseButton.LeftButton)
    )
    in_s = timeline._regions[op.id].start_seconds()
    assert in_s != 2.0
    assert timeline.playhead_seconds() == 4.0
    assert previews[-1] == in_s


def test_trimming_right_handle_snaps_out_to_playhead() -> None:
    op = _mute_op(2.0, 5.0)
    timeline = _shown_timeline(op)
    timeline.set_playhead(6.0, notify=False)
    press_pos = _viewport_pos(timeline, 5.0, x_nudge=-2.0)
    timeline.mousePressEvent(_view_mouse(QEvent.Type.MouseButtonPress, press_pos))
    timeline.mouseMoveEvent(
        _view_mouse(
            QEvent.Type.MouseMove,
            _viewport_pos(timeline, 6.05),
            buttons=Qt.MouseButton.LeftButton,
        )
    )
    item = timeline._regions[op.id]
    assert item.end_seconds() == pytest.approx(6.0)
    assert item.start_seconds() == pytest.approx(2.0)
    assert timeline.playhead_seconds() == pytest.approx(6.0)


def test_trimming_right_handle_releases_playhead_snap() -> None:
    op = _mute_op(2.0, 5.0)
    timeline = _shown_timeline(op)
    timeline.set_playhead(6.0, notify=False)
    press_pos = _viewport_pos(timeline, 5.0, x_nudge=-2.0)
    timeline.mousePressEvent(_view_mouse(QEvent.Type.MouseButtonPress, press_pos))
    timeline.mouseMoveEvent(
        _view_mouse(
            QEvent.Type.MouseMove,
            _viewport_pos(timeline, 6.05),
            buttons=Qt.MouseButton.LeftButton,
        )
    )
    item = timeline._regions[op.id]
    assert item.end_seconds() == pytest.approx(6.0)
    for time_s in (6.1, 6.15, 6.2, 6.3, 6.6, 7.0):
        timeline.mouseMoveEvent(
            _view_mouse(
                QEvent.Type.MouseMove,
                _viewport_pos(timeline, time_s),
                buttons=Qt.MouseButton.LeftButton,
            )
        )
    assert item.end_seconds() == pytest.approx(7.0, abs=0.05)
    assert item.start_seconds() == pytest.approx(2.0)


def test_trimming_left_handle_snaps_in_to_playhead() -> None:
    op = _mute_op(2.0, 5.0)
    timeline = _shown_timeline(op)
    timeline.set_playhead(3.5, notify=False)
    press_pos = _viewport_pos(timeline, 2.0, x_nudge=2.0)
    timeline.mousePressEvent(_view_mouse(QEvent.Type.MouseButtonPress, press_pos))
    timeline.mouseMoveEvent(
        _view_mouse(
            QEvent.Type.MouseMove,
            _viewport_pos(timeline, 3.45),
            buttons=Qt.MouseButton.LeftButton,
        )
    )
    item = timeline._regions[op.id]
    assert item.start_seconds() == pytest.approx(3.5)
    assert item.end_seconds() == pytest.approx(5.0)
    assert timeline.playhead_seconds() == pytest.approx(3.5)


def test_clicking_region_body_does_not_seek_playhead() -> None:
    op = _mute_op(2.0, 5.0)
    timeline = _shown_timeline(op)
    timeline.set_playhead(0.5, notify=False)
    playheads: list[float] = []
    timeline.playhead_changed.connect(playheads.append)
    body = _viewport_pos(timeline, 3.5)
    timeline.mousePressEvent(_view_mouse(QEvent.Type.MouseButtonPress, body))
    timeline.mouseReleaseEvent(
        _view_mouse(
            QEvent.Type.MouseButtonRelease,
            body,
            buttons=Qt.MouseButton.NoButton,
        )
    )
    assert timeline.playhead_seconds() == 0.5
    assert playheads == []
    assert timeline.selected_operation_id() == op.id


def test_dragging_region_snaps_end_to_playhead() -> None:
    op = _mute_op(2.0, 5.0)
    timeline = _shown_timeline(op)
    timeline.set_playhead(8.0, notify=False)
    press = _viewport_pos(timeline, 3.5)
    timeline.mousePressEvent(_view_mouse(QEvent.Type.MouseButtonPress, press))
    drag = _viewport_pos(timeline, 6.55)
    timeline.mouseMoveEvent(
        _view_mouse(QEvent.Type.MouseMove, drag, buttons=Qt.MouseButton.LeftButton)
    )
    item = timeline._regions[op.id]
    assert item.end_seconds() == pytest.approx(8.0)
    assert item.start_seconds() == pytest.approx(5.0)


def test_dragging_region_releases_playhead_snap() -> None:
    op = _mute_op(2.0, 5.0)
    timeline = _shown_timeline(op)
    timeline.set_playhead(8.0, notify=False)
    press = _viewport_pos(timeline, 3.5)
    timeline.mousePressEvent(_view_mouse(QEvent.Type.MouseButtonPress, press))
    timeline.mouseMoveEvent(
        _view_mouse(
            QEvent.Type.MouseMove,
            _viewport_pos(timeline, 6.55),
            buttons=Qt.MouseButton.LeftButton,
        )
    )
    item = timeline._regions[op.id]
    assert item.end_seconds() == pytest.approx(8.0)
    for time_s in (6.6, 6.65, 6.7, 6.75, 6.8, 6.9, 7.2):
        timeline.mouseMoveEvent(
            _view_mouse(
                QEvent.Type.MouseMove,
                _viewport_pos(timeline, time_s),
                buttons=Qt.MouseButton.LeftButton,
            )
        )
    # Mouse has pulled the unconstrained out-point well past the magnet.
    assert item.end_seconds() == pytest.approx(5.0 + (7.2 - 3.5))
    assert item.start_seconds() == pytest.approx(2.0 + (7.2 - 3.5))


def test_dragging_region_snaps_start_to_playhead() -> None:
    op = _mute_op(2.0, 5.0)
    timeline = _shown_timeline(op)
    timeline.set_playhead(6.0, notify=False)
    press = _viewport_pos(timeline, 3.5)
    timeline.mousePressEvent(_view_mouse(QEvent.Type.MouseButtonPress, press))
    drag = _viewport_pos(timeline, 7.54)
    timeline.mouseMoveEvent(
        _view_mouse(QEvent.Type.MouseMove, drag, buttons=Qt.MouseButton.LeftButton)
    )
    item = timeline._regions[op.id]
    assert item.start_seconds() == pytest.approx(6.0)
    assert item.end_seconds() == pytest.approx(9.0)


def test_dragging_region_does_not_snap_when_magnet_off() -> None:
    op = _mute_op(2.0, 5.0)
    timeline = _shown_timeline(op)
    timeline.set_magnetic_snap_enabled(False)
    timeline.set_playhead(8.0, notify=False)
    press = _viewport_pos(timeline, 3.5)
    timeline.mousePressEvent(_view_mouse(QEvent.Type.MouseButtonPress, press))
    drag = _viewport_pos(timeline, 6.55)
    timeline.mouseMoveEvent(
        _view_mouse(QEvent.Type.MouseMove, drag, buttons=Qt.MouseButton.LeftButton)
    )
    item = timeline._regions[op.id]
    assert item.end_seconds() != pytest.approx(8.0)
    assert item.end_seconds() == pytest.approx(5.0 + (6.55 - 3.5))


def test_dragging_region_snaps_end_to_other_region() -> None:
    moving = _mute_op(2.0, 5.0)
    other = _mute_op(8.0, 10.0)
    timeline = _shown_timeline([moving, other])
    timeline.set_playhead(0.5, notify=False)
    press = _viewport_pos(timeline, 3.5)
    timeline.mousePressEvent(_view_mouse(QEvent.Type.MouseButtonPress, press))
    drag = _viewport_pos(timeline, 6.55)
    timeline.mouseMoveEvent(
        _view_mouse(QEvent.Type.MouseMove, drag, buttons=Qt.MouseButton.LeftButton)
    )
    item = timeline._regions[moving.id]
    assert item.end_seconds() == pytest.approx(8.0)
    assert item.start_seconds() == pytest.approx(5.0)


def test_dragging_region_snaps_start_to_other_region_end() -> None:
    moving = _mute_op(5.0, 8.0)
    other = _mute_op(2.0, 4.0)
    timeline = _shown_timeline([moving, other])
    timeline.set_playhead(0.5, notify=False)
    press = _viewport_pos(timeline, 6.5)
    timeline.mousePressEvent(_view_mouse(QEvent.Type.MouseButtonPress, press))
    drag = _viewport_pos(timeline, 5.55)
    timeline.mouseMoveEvent(
        _view_mouse(QEvent.Type.MouseMove, drag, buttons=Qt.MouseButton.LeftButton)
    )
    item = timeline._regions[moving.id]
    assert item.start_seconds() == pytest.approx(4.0)
    assert item.end_seconds() == pytest.approx(7.0)


def test_dragging_region_does_not_snap_to_other_when_magnet_off() -> None:
    moving = _mute_op(2.0, 5.0)
    other = _mute_op(8.0, 10.0)
    timeline = _shown_timeline([moving, other])
    timeline.set_magnetic_snap_enabled(False)
    timeline.set_playhead(0.5, notify=False)
    press = _viewport_pos(timeline, 3.5)
    timeline.mousePressEvent(_view_mouse(QEvent.Type.MouseButtonPress, press))
    drag = _viewport_pos(timeline, 6.55)
    timeline.mouseMoveEvent(
        _view_mouse(QEvent.Type.MouseMove, drag, buttons=Qt.MouseButton.LeftButton)
    )
    item = timeline._regions[moving.id]
    assert item.end_seconds() != pytest.approx(8.0)
    assert item.end_seconds() == pytest.approx(5.0 + (6.55 - 3.5))


def test_trimming_right_handle_snaps_to_other_region() -> None:
    trimming = _mute_op(2.0, 5.0)
    other = _mute_op(6.0, 8.0)
    timeline = _shown_timeline([trimming, other])
    timeline.set_playhead(0.5, notify=False)
    press_pos = _viewport_pos(timeline, 5.0, x_nudge=-2.0)
    timeline.mousePressEvent(_view_mouse(QEvent.Type.MouseButtonPress, press_pos))
    timeline.mouseMoveEvent(
        _view_mouse(
            QEvent.Type.MouseMove,
            _viewport_pos(timeline, 6.05),
            buttons=Qt.MouseButton.LeftButton,
        )
    )
    item = timeline._regions[trimming.id]
    assert item.end_seconds() == pytest.approx(6.0)
    assert item.start_seconds() == pytest.approx(2.0)


def test_trimming_left_handle_snaps_to_other_region() -> None:
    trimming = _mute_op(5.0, 8.0)
    other = _mute_op(2.0, 4.0)
    timeline = _shown_timeline([trimming, other])
    timeline.set_playhead(0.5, notify=False)
    press_pos = _viewport_pos(timeline, 5.0, x_nudge=2.0)
    timeline.mousePressEvent(_view_mouse(QEvent.Type.MouseButtonPress, press_pos))
    timeline.mouseMoveEvent(
        _view_mouse(
            QEvent.Type.MouseMove,
            _viewport_pos(timeline, 3.95),
            buttons=Qt.MouseButton.LeftButton,
        )
    )
    item = timeline._regions[trimming.id]
    assert item.start_seconds() == pytest.approx(4.0)
    assert item.end_seconds() == pytest.approx(8.0)


def test_keyboard_alt_nudge_previews_in_without_moving_playhead() -> None:
    op = _mute_op(2.0, 5.0)
    timeline = _shown_timeline(op)
    timeline.set_playhead(4.0, notify=False)
    timeline.select_operation(op.id)
    playheads: list[float] = []
    finished: list[float] = []
    timeline.playhead_changed.connect(playheads.append)
    timeline.trim_preview_finished.connect(finished.append)

    from PySide6.QtGui import QKeyEvent

    press = QKeyEvent(
        QEvent.Type.KeyPress,
        Qt.Key.Key_Right,
        Qt.KeyboardModifier.AltModifier,
    )
    timeline.keyPressEvent(press)
    in_s = timeline._regions[op.id].start_seconds()
    assert in_s > 2.0
    assert timeline.playhead_seconds() == 4.0
    assert playheads == []
    assert finished == [in_s]


def test_keyboard_ctrl_nudge_previews_out_without_moving_playhead() -> None:
    op = _mute_op(2.0, 5.0)
    timeline = _shown_timeline(op)
    timeline.set_playhead(1.0, notify=False)
    timeline.select_operation(op.id)
    finished: list[float] = []
    timeline.trim_preview_finished.connect(finished.append)

    from PySide6.QtGui import QKeyEvent

    press = QKeyEvent(
        QEvent.Type.KeyPress,
        Qt.Key.Key_Right,
        Qt.KeyboardModifier.ControlModifier,
    )
    timeline.keyPressEvent(press)
    out_s = timeline._regions[op.id].end_seconds()
    assert out_s > 5.0
    assert timeline.playhead_seconds() == 1.0
    assert finished == [out_s]


def test_overlapping_mute_and_cover_hit_by_lane() -> None:
    mute = _mute_op(2.0, 5.0)
    cover = _cover_op(2.0, 5.0)
    timeline = _shown_timeline([mute, cover])

    video_pos = _viewport_pos(timeline, 3.5, scene_y=video_lane_mid_y())
    timeline.mousePressEvent(_view_mouse(QEvent.Type.MouseButtonPress, video_pos))
    assert timeline.selected_operation_id() == cover.id

    audio_pos = _viewport_pos(timeline, 3.5, scene_y=audio_lane_mid_y())
    timeline.mousePressEvent(_view_mouse(QEvent.Type.MouseButtonPress, audio_pos))
    assert timeline.selected_operation_id() == mute.id


def _blur_op(start_s: float, end_s: float) -> Operation:
    return Operation.create(
        OperationType.BLUR,
        -1,
        [],
        TimePoint.from_seconds_float(start_s, 48000),
        TimePoint.from_seconds_float(end_s, 48000),
    )


def test_alt_click_cycles_overlapping_same_lane_regions() -> None:
    first = _cover_op(2.0, 5.0)
    second = _blur_op(2.0, 5.0)
    timeline = _shown_timeline([first, second])
    pos = _viewport_pos(timeline, 3.5, scene_y=video_lane_mid_y())

    timeline.mousePressEvent(_view_mouse(QEvent.Type.MouseButtonPress, pos))
    first_pick = timeline.selected_operation_id()
    assert first_pick in {first.id, second.id}

    alt_press = QMouseEvent(
        QEvent.Type.MouseButtonPress,
        pos,
        pos,
        QPointF(),
        Qt.MouseButton.LeftButton,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.AltModifier,
    )
    timeline.mousePressEvent(alt_press)
    second_pick = timeline.selected_operation_id()
    assert second_pick in {first.id, second.id}
    assert second_pick != first_pick

    timeline.mousePressEvent(alt_press)
    assert timeline.selected_operation_id() == first_pick


def test_ruler_click_seeks_and_does_not_create_region() -> None:
    timeline = _shown_timeline([])
    created: list[tuple[str, float, float]] = []
    timeline.operation_created.connect(lambda t, a, b: created.append((t, a, b)))

    press_pos = _viewport_pos(timeline, 4.0, scene_y=8.0)
    timeline.mousePressEvent(_view_mouse(QEvent.Type.MouseButtonPress, press_pos))
    assert timeline.playhead_seconds() == 4.0

    drag_pos = _viewport_pos(timeline, 6.0, scene_y=8.0)
    timeline.mouseMoveEvent(
        _view_mouse(QEvent.Type.MouseMove, drag_pos, buttons=Qt.MouseButton.LeftButton)
    )
    timeline.mouseReleaseEvent(
        _view_mouse(QEvent.Type.MouseButtonRelease, drag_pos, buttons=Qt.MouseButton.NoButton)
    )
    assert created == []
    assert timeline.playhead_seconds() == 6.0


def test_empty_click_seeks_without_creating() -> None:
    timeline = _shown_timeline([])
    timeline.set_playhead(1.0, notify=False)
    created: list[tuple[str, float, float]] = []
    timeline.operation_created.connect(lambda t, a, b: created.append((t, a, b)))

    pos = _viewport_pos(timeline, 4.0)
    timeline.mousePressEvent(_view_mouse(QEvent.Type.MouseButtonPress, pos))
    assert timeline.playhead_seconds() == 1.0
    timeline.mouseReleaseEvent(
        _view_mouse(QEvent.Type.MouseButtonRelease, pos, buttons=Qt.MouseButton.NoButton)
    )
    assert created == []
    assert timeline.playhead_seconds() == pytest.approx(4.0, abs=0.05)


def test_empty_timeline_fills_only_the_visible_width() -> None:
    timeline = TimelineWidget()
    timeline.resize(900, 400)
    timeline.show()
    _app.processEvents()
    timeline.set_edits_enabled(False)

    assert timeline.horizontalScrollBar().maximum() == 0
    assert timeline._content_width() == pytest.approx(timeline.viewport().width(), abs=1.0)  # noqa: SLF001

    timeline.resize(1100, 420)
    _app.processEvents()
    assert timeline.horizontalScrollBar().maximum() == 0
    assert timeline._content_width() == pytest.approx(timeline.viewport().width(), abs=1.0)  # noqa: SLF001


def test_timeline_lanes_are_flush_with_the_view_edges() -> None:
    timeline = TimelineWidget()
    assert timeline.frameShape() == QFrame.Shape.NoFrame
    assert timeline.alignment() == (Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)

    timeline.set_duration(600)
    timeline.resize(900, 400)
    timeline.show()
    _app.processEvents()
    assert timeline.sceneRect().width() == timeline.viewport().width()

    timeline.resize(1100, 420)
    # Resize layout is deferred (~16ms); scrollbar can also shrink the viewport after.
    for _ in range(30):
        _app.processEvents()
        if timeline._resize_in_progress:  # noqa: SLF001
            timeline._finalize_resize()  # noqa: SLF001
            _app.processEvents()
        if abs(timeline.sceneRect().width() - float(timeline.viewport().width())) < 0.5:
            break
        QTest.qWait(20)
    assert timeline.sceneRect().width() == pytest.approx(float(timeline.viewport().width()), abs=0.5)


def test_edits_disabled_drag_seeks_without_creating() -> None:
    timeline = _shown_timeline([])
    timeline.set_edits_enabled(False)
    timeline.set_playhead(1.0, notify=False)
    created: list[tuple[str, float, float]] = []
    timeline.operation_created.connect(lambda t, a, b: created.append((t, a, b)))

    press_pos = _viewport_pos(timeline, 2.0)
    timeline.mousePressEvent(_view_mouse(QEvent.Type.MouseButtonPress, press_pos))
    drag_pos = _viewport_pos(timeline, 5.0)
    timeline.mouseMoveEvent(
        _view_mouse(QEvent.Type.MouseMove, drag_pos, buttons=Qt.MouseButton.LeftButton)
    )
    timeline.mouseReleaseEvent(
        _view_mouse(QEvent.Type.MouseButtonRelease, drag_pos, buttons=Qt.MouseButton.NoButton)
    )
    assert created == []
    assert timeline.playhead_seconds() == pytest.approx(1.0)


def test_drawing_region_does_not_move_playhead() -> None:
    timeline = _shown_timeline([])
    timeline.set_playhead(1.0, notify=False)
    created: list[tuple[str, float, float]] = []
    timeline.operation_created.connect(lambda t, a, b: created.append((t, a, b)))

    press_pos = _viewport_pos(timeline, 2.0)
    timeline.mousePressEvent(_view_mouse(QEvent.Type.MouseButtonPress, press_pos))
    assert timeline.playhead_seconds() == 1.0

    drag_pos = _viewport_pos(timeline, 5.0)
    timeline.mouseMoveEvent(
        _view_mouse(QEvent.Type.MouseMove, drag_pos, buttons=Qt.MouseButton.LeftButton)
    )
    timeline.mouseReleaseEvent(
        _view_mouse(QEvent.Type.MouseButtonRelease, drag_pos, buttons=Qt.MouseButton.NoButton)
    )
    assert len(created) == 1
    assert created[0][0] == OperationType.MUTE.value
    assert timeline.playhead_seconds() == 1.0


def test_video_lane_draw_uses_last_video_type() -> None:
    timeline = _shown_timeline([])
    timeline.set_draw_operation_type(OperationType.BLUR)
    created: list[tuple[str, float, float]] = []
    timeline.operation_created.connect(lambda t, a, b: created.append((t, a, b)))

    press_pos = _viewport_pos(timeline, 2.0, scene_y=video_lane_mid_y())
    timeline.mousePressEvent(_view_mouse(QEvent.Type.MouseButtonPress, press_pos))
    drag_pos = _viewport_pos(timeline, 5.0, scene_y=video_lane_mid_y())
    timeline.mouseMoveEvent(
        _view_mouse(QEvent.Type.MouseMove, drag_pos, buttons=Qt.MouseButton.LeftButton)
    )
    timeline.mouseReleaseEvent(
        _view_mouse(QEvent.Type.MouseButtonRelease, drag_pos, buttons=Qt.MouseButton.NoButton)
    )
    assert len(created) == 1
    assert created[0][0] == OperationType.BLUR.value
    assert created[0][2] - created[0][1] >= 2.0


def _right_drag_region(
    timeline: TimelineWidget,
    start_s: float,
    end_s: float,
    *,
    scene_y: float,
) -> None:
    press_pos = _viewport_pos(timeline, start_s, scene_y=scene_y)
    drag_pos = _viewport_pos(timeline, end_s, scene_y=scene_y)
    timeline.mousePressEvent(
        _view_mouse(
            QEvent.Type.MouseButtonPress,
            press_pos,
            button=Qt.MouseButton.RightButton,
            buttons=Qt.MouseButton.RightButton,
        )
    )
    timeline.mouseMoveEvent(
        _view_mouse(
            QEvent.Type.MouseMove,
            drag_pos,
            button=Qt.MouseButton.RightButton,
            buttons=Qt.MouseButton.RightButton,
        )
    )
    timeline.mouseReleaseEvent(
        _view_mouse(
            QEvent.Type.MouseButtonRelease,
            drag_pos,
            button=Qt.MouseButton.RightButton,
            buttons=Qt.MouseButton.NoButton,
        )
    )


def test_right_drag_on_video_lane_creates_cover_when_audio_tool_is_active() -> None:
    timeline = _shown_timeline([])
    timeline.set_timeline_mode(TimelineMode.SELECT)
    timeline.set_draw_operation_type(OperationType.MUTE)
    created: list[tuple[str, float, float]] = []
    timeline.operation_created.connect(lambda t, a, b: created.append((t, a, b)))

    _right_drag_region(timeline, 2.0, 5.0, scene_y=video_lane_mid_y())
    assert len(created) == 1
    assert created[0][0] == OperationType.COVER.value
    assert created[0][2] - created[0][1] >= 2.0


def test_right_drag_on_video_lane_uses_last_video_tool() -> None:
    timeline = _shown_timeline([])
    timeline.set_draw_operation_type(OperationType.BLUR)
    timeline.set_draw_operation_type(OperationType.MUTE)
    created: list[tuple[str, float, float]] = []
    timeline.operation_created.connect(lambda t, a, b: created.append((t, a, b)))

    _right_drag_region(timeline, 2.0, 5.0, scene_y=video_lane_mid_y())
    assert len(created) == 1
    assert created[0][0] == OperationType.BLUR.value


def test_playhead_draw_x_snaps_to_pixel_center() -> None:
    assert playhead_draw_x(100.2) == 100.5
    assert playhead_draw_x(100.8) == 101.5


def test_timeline_maximum_height_is_both_tracks_fully_open() -> None:
    timeline = TimelineWidget()
    assert timeline.maximumHeight() == timeline.height_for_lanes(MAX_LANE_HEIGHT)
    assert timeline.minimumHeight() == timeline.height_for_lanes(MIN_LANE_HEIGHT)
    assert timeline.maximumHeight() > timeline.minimumHeight()


def test_shrinking_timeline_shrinks_both_tracks_together() -> None:
    timeline = _shown_timeline([_mute_op(1.0, 3.0), _cover_op(1.0, 3.0)])
    assert timeline._video_bg.rect().height() == pytest.approx(LANE_HEIGHT)  # noqa: SLF001
    assert timeline._waveform.boundingRect().height() == pytest.approx(LANE_HEIGHT)  # noqa: SLF001

    timeline.resize(640, timeline.minimumHeight())
    _app.processEvents()
    video_h = timeline._video_bg.rect().height()  # noqa: SLF001
    audio_h = timeline._waveform.boundingRect().height()  # noqa: SLF001
    assert audio_h == pytest.approx(video_h)
    assert video_h == pytest.approx(MIN_LANE_HEIGHT, abs=2.0)
    heights = [item.rect().height() for item in timeline._regions.values()]  # noqa: SLF001
    assert len(heights) == 2
    assert heights[0] == pytest.approx(heights[1])

    timeline.resize(640, timeline.maximumHeight())
    _app.processEvents()
    assert timeline._video_bg.rect().height() == pytest.approx(MAX_LANE_HEIGHT)  # noqa: SLF001
    assert timeline._waveform.boundingRect().height() == pytest.approx(MAX_LANE_HEIGHT)  # noqa: SLF001


def test_playhead_line_starts_below_ruler_with_half_diamond_cap() -> None:
    timeline = _shown_timeline([])
    timeline.set_playhead(2.0, notify=False)
    line = timeline._playhead_line.line()
    assert line.y1() == RULER_HEIGHT
    assert line.y2() == SCENE_HEIGHT
    assert line.x1() == playhead_draw_x(timeline._time_to_scene_x(2.0))
    head = timeline._playhead_head.polygon()
    expected = playhead_head_polygon(line.x1(), RULER_HEIGHT)
    assert head.size() == 3
    assert head == expected
    assert all(point.y() <= RULER_HEIGHT for point in head)


def test_right_drag_on_audio_lane_still_creates_audio_region() -> None:
    timeline = _shown_timeline([])
    timeline.set_draw_operation_type(OperationType.BLUR)
    created: list[tuple[str, float, float]] = []
    timeline.operation_created.connect(lambda t, a, b: created.append((t, a, b)))

    _right_drag_region(timeline, 2.0, 5.0, scene_y=audio_lane_mid_y())
    assert len(created) == 1
    assert created[0][0] == OperationType.MUTE.value


def test_dragging_region_snaps_end_to_scene_cut() -> None:
    op = _mute_op(2.0, 5.0)
    timeline = _shown_timeline(op)
    timeline.set_playhead(0.5, notify=False)
    timeline.set_scene_cut_times((8.0,))
    timeline.set_snap_to_scene_cuts_enabled(True)
    press = _viewport_pos(timeline, 3.5)
    timeline.mousePressEvent(_view_mouse(QEvent.Type.MouseButtonPress, press))
    drag = _viewport_pos(timeline, 6.55)
    timeline.mouseMoveEvent(
        _view_mouse(QEvent.Type.MouseMove, drag, buttons=Qt.MouseButton.LeftButton)
    )
    item = timeline._regions[op.id]
    assert item.end_seconds() == pytest.approx(8.0)
    assert item.start_seconds() == pytest.approx(5.0)


def test_dragging_region_does_not_snap_to_scene_cut_when_toggle_off() -> None:
    op = _mute_op(2.0, 5.0)
    timeline = _shown_timeline(op)
    timeline.set_playhead(0.5, notify=False)
    timeline.set_scene_cut_times((8.0,))
    timeline.set_snap_to_scene_cuts_enabled(False)
    item = timeline._regions[op.id]
    assert 8.0 not in item._magnetic_snap_targets_s()  # noqa: SLF001
    press = _viewport_pos(timeline, 3.5)
    timeline.mousePressEvent(_view_mouse(QEvent.Type.MouseButtonPress, press))
    drag = _viewport_pos(timeline, 6.55)
    timeline.mouseMoveEvent(
        _view_mouse(QEvent.Type.MouseMove, drag, buttons=Qt.MouseButton.LeftButton)
    )
    assert item.end_seconds() != pytest.approx(8.0)
    assert item.end_seconds() == pytest.approx(5.0 + (6.55 - 3.5))


def test_drag_reuses_sorted_snap_targets() -> None:
    op = _mute_op(2.0, 5.0)
    timeline = _shown_timeline(op)
    timeline.set_playhead(0.5, notify=False)
    timeline.set_scene_cut_times(tuple(i * 0.25 for i in range(1, 400)))
    timeline.set_snap_to_scene_cuts_enabled(True)
    press = _viewport_pos(timeline, 3.5)
    timeline.mousePressEvent(_view_mouse(QEvent.Type.MouseButtonPress, press))
    drag = _viewport_pos(timeline, 4.2)
    timeline.mouseMoveEvent(
        _view_mouse(QEvent.Type.MouseMove, drag, buttons=Qt.MouseButton.LeftButton)
    )
    item = timeline._regions[op.id]
    cached = item._snap_target_cache  # noqa: SLF001
    assert cached is not None
    assert list(cached) == sorted(cached)
    assert 8.0 in cached
    drag_again = _viewport_pos(timeline, 4.8)
    timeline.mouseMoveEvent(
        _view_mouse(QEvent.Type.MouseMove, drag_again, buttons=Qt.MouseButton.LeftButton)
    )
    assert item._snap_target_cache is cached  # noqa: SLF001
    timeline.mouseReleaseEvent(_view_mouse(QEvent.Type.MouseButtonRelease, drag_again))
    assert item._snap_target_cache is None  # noqa: SLF001


def test_disabling_scene_cuts_clears_region_cut_targets() -> None:
    op = _mute_op(2.0, 5.0)
    timeline = _shown_timeline(op)
    timeline.set_scene_cut_times((8.0, 12.0))
    timeline.set_snap_to_scene_cuts_enabled(True)
    item = timeline._regions[op.id]
    assert 8.0 in item._magnetic_snap_targets_s()  # noqa: SLF001
    timeline.set_snap_to_scene_cuts_enabled(False)
    targets = item._magnetic_snap_targets_s()  # noqa: SLF001
    assert 8.0 not in targets
    assert 12.0 not in targets
    assert item._scene_cut_times == ()  # noqa: SLF001


def test_trimming_right_handle_snaps_to_scene_cut() -> None:
    op = _mute_op(2.0, 5.0)
    timeline = _shown_timeline(op)
    timeline.set_playhead(0.5, notify=False)
    timeline.set_scene_cut_times((6.0,))
    timeline.set_snap_to_scene_cuts_enabled(True)
    press_pos = _viewport_pos(timeline, 5.0, x_nudge=-2.0)
    timeline.mousePressEvent(_view_mouse(QEvent.Type.MouseButtonPress, press_pos))
    timeline.mouseMoveEvent(
        _view_mouse(
            QEvent.Type.MouseMove,
            _viewport_pos(timeline, 6.05),
            buttons=Qt.MouseButton.LeftButton,
        )
    )
    item = timeline._regions[op.id]
    assert item.end_seconds() == pytest.approx(6.0)
