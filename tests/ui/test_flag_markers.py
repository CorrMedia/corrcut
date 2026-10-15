from PySide6.QtCore import QEvent, QPointF, Qt
from PySide6.QtGui import QColor, QKeyEvent, QMouseEvent
from PySide6.QtWidgets import QApplication

from corrcut.domain.timeline_flag import TimelineFlag
from corrcut.ui.flag_slots import FlagSlotStyle
from corrcut.ui.timeline.timeline_widget import TimelineWidget

_app = QApplication.instance() or QApplication([])


def _styles() -> dict[int, FlagSlotStyle]:
    return {
        1: FlagSlotStyle(1, QColor("#e44545"), "Language"),
        2: FlagSlotStyle(2, QColor("#e8c43a"), "Violence"),
        3: FlagSlotStyle(3, QColor("#3da5e8"), "Review"),
    }


def _mouse(
    widget: TimelineWidget,
    etype: QEvent.Type,
    view_pos: QPointF,
    button: Qt.MouseButton = Qt.MouseButton.LeftButton,
    buttons: Qt.MouseButton | None = None,
) -> None:
    held = button if buttons is None else buttons
    event = QMouseEvent(
        etype,
        view_pos,
        view_pos,
        QPointF(),
        button,
        held,
        Qt.KeyboardModifier.NoModifier,
    )
    if etype == QEvent.Type.MouseButtonPress:
        widget.mousePressEvent(event)
    elif etype == QEvent.Type.MouseMove:
        widget.mouseMoveEvent(event)
    else:
        widget.mouseReleaseEvent(event)


def _item_view_pos(widget: TimelineWidget, item) -> QPointF:  # noqa: ANN001
    scene_pos = item.mapToScene(item.boundingRect().center())
    return QPointF(widget.mapFromScene(scene_pos))


def test_sync_flags_uses_custom_color() -> None:
    widget = TimelineWidget()
    flag = TimelineFlag.create(10.0, 1)
    flag.color = "#00ff00"
    widget.sync_flags([flag], _styles())
    assert widget._flags[flag.id].color().name() == "#00ff00"
    assert widget._flags[flag.id].slot() == 1

    flag.slot = 2
    flag.color = None
    widget.sync_flags([flag], _styles())
    assert widget._flags[flag.id].color().name() == "#e8c43a"
    assert widget._flags[flag.id].slot() == 2


def test_sync_flags_creates_and_updates_markers() -> None:
    widget = TimelineWidget()
    widget.set_duration(60.0)
    flags = [TimelineFlag.create(10.0, 1), TimelineFlag.create(20.0, 2)]
    widget.sync_flags(flags, _styles())
    assert len(widget._flags) == 2
    assert widget._flags[flags[0].id].seconds() == 10.0

    flags[0].seconds = 12.0
    widget.sync_flags(flags, _styles())
    assert widget._flags[flags[0].id].seconds() == 12.0
    widget.sync_flags([], _styles())
    assert widget._flags == {}


def test_clicking_flag_selects_and_seeks() -> None:
    widget = TimelineWidget()
    widget.resize(800, 220)
    widget.show()
    widget.set_duration(60.0)
    widget.set_pixels_per_second(20.0)
    _app.processEvents()
    flag = TimelineFlag.create(5.0, 1)
    widget.sync_flags([flag], _styles())
    item = widget._flags[flag.id]
    _mouse(widget, QEvent.Type.MouseButtonPress, _item_view_pos(widget, item))
    assert widget.selected_flag_id() == flag.id
    assert abs(widget.playhead_seconds() - 5.0) < 0.05


def test_right_clicking_flag_does_not_seek(qtbot) -> None:
    widget = TimelineWidget()
    qtbot.addWidget(widget)
    widget.resize(800, 220)
    widget.show()
    widget.set_duration(60.0)
    widget.set_pixels_per_second(20.0)
    widget.set_playhead(20.0)
    _app.processEvents()
    flag = TimelineFlag.create(5.0, 1)
    widget.sync_flags([flag], _styles())
    item = widget._flags[flag.id]
    with qtbot.waitSignal(widget.flag_context_menu_requested, timeout=1000) as blocker:
        _mouse(
            widget,
            QEvent.Type.MouseButtonPress,
            _item_view_pos(widget, item),
            button=Qt.MouseButton.RightButton,
        )
    assert blocker.args[0] == flag.id
    assert widget.selected_flag_id() == flag.id
    assert abs(widget.playhead_seconds() - 20.0) < 1e-6


def test_dragging_flag_moves_time() -> None:
    widget = TimelineWidget()
    widget.resize(800, 220)
    widget.show()
    widget.set_duration(60.0)
    widget.set_pixels_per_second(20.0)
    _app.processEvents()
    flag = TimelineFlag.create(5.0, 1)
    widget.sync_flags([flag], _styles())
    item = widget._flags[flag.id]
    moved: list[tuple[str, float, bool]] = []
    widget.flag_moved.connect(lambda fid, seconds, final: moved.append((fid, seconds, final)))
    press = _item_view_pos(widget, item)
    _mouse(widget, QEvent.Type.MouseButtonPress, press)
    _mouse(widget, QEvent.Type.MouseMove, QPointF(160.0, press.y()), buttons=Qt.MouseButton.LeftButton)
    _mouse(widget, QEvent.Type.MouseButtonRelease, QPointF(160.0, press.y()))
    assert abs(item.seconds() - 8.0) < 0.05
    assert abs(widget.playhead_seconds() - 8.0) < 0.05
    assert moved
    assert moved[-1] == (flag.id, item.seconds(), True)
    assert any(not final for _, _, final in moved)


def test_clicking_flag_ignores_tiny_pointer_jitter() -> None:
    widget = TimelineWidget()
    widget.resize(800, 220)
    widget.show()
    widget.set_duration(60.0)
    widget.set_pixels_per_second(20.0)
    _app.processEvents()
    flag = TimelineFlag.create(5.0, 1)
    widget.sync_flags([flag], _styles())
    item = widget._flags[flag.id]
    moved: list[tuple[str, float, bool]] = []
    widget.flag_moved.connect(lambda fid, seconds, final: moved.append((fid, seconds, final)))
    press = _item_view_pos(widget, item)
    _mouse(widget, QEvent.Type.MouseButtonPress, press)
    _mouse(
        widget,
        QEvent.Type.MouseMove,
        QPointF(press.x() + 1.0, press.y()),
        buttons=Qt.MouseButton.LeftButton,
    )
    _mouse(widget, QEvent.Type.MouseButtonRelease, QPointF(press.x() + 1.0, press.y()))
    assert abs(item.seconds() - 5.0) < 1e-6
    assert abs(widget.playhead_seconds() - 5.0) < 1e-6
    assert moved == []


def test_flag_tip_aligns_with_playhead() -> None:
    from corrcut.ui.timeline.flag_marker_item import flag_draw_x
    from corrcut.ui.timeline.timeline_widget import playhead_draw_x

    widget = TimelineWidget()
    widget.set_duration(60.0)
    widget.set_pixels_per_second(20.0)
    flag = TimelineFlag.create(5.0, 1)
    widget.sync_flags([flag], _styles())
    widget.set_playhead(5.0)
    item = widget._flags[flag.id]
    expected = playhead_draw_x(widget._time_to_scene_x(5.0))
    assert item.pos().x() == expected
    assert item.pos().x() == flag_draw_x(5.0 * 20.0)


def test_ribbon_shape_is_solid_bookmark() -> None:
    from corrcut.ui.timeline.flag_marker_item import ribbon_polygon

    body = ribbon_polygon(0.0)
    assert body.count() == 5
    xs = [body.at(i).x() for i in range(body.count())]
    ys = [body.at(i).y() for i in range(body.count())]
    assert min(xs) == -6.0
    assert max(xs) == 6.0
    assert min(xs) < 0 < max(xs)
    assert ys[-2] == max(ys)


def test_delete_key_emits_flag_delete() -> None:
    widget = TimelineWidget()
    flag = TimelineFlag.create(3.0, 3)
    widget.sync_flags([flag], _styles())
    widget.select_flag(flag.id)
    deleted: list[str] = []
    widget.flag_delete_requested.connect(deleted.append)
    widget.keyPressEvent(
        QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Delete, Qt.KeyboardModifier.NoModifier)
    )
    assert deleted == [flag.id]
