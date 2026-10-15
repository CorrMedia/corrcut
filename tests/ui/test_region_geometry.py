"""Unit tests for timeline region time-range edits."""

import pytest
from PySide6.QtCore import QEvent, QPointF, QRectF, Qt
from PySide6.QtGui import QMouseEvent
from PySide6.QtWidgets import QApplication, QGraphicsScene, QGraphicsSceneHoverEvent

from corrcut.domain.operation import OperationType
from corrcut.ui.timeline.region_geometry import (
    apply_move,
    apply_resize_left,
    apply_resize_right,
    nudge_range,
    playhead_snap_threshold_s,
    snap_moved_range_to_playhead,
    snap_moved_range_to_targets,
    snap_resized_edge_to_playhead,
    snap_resized_edge_to_targets,
    snap_time,
)
from corrcut.ui.timeline.region_item import RegionItem, region_type_label

_app = QApplication.instance() or QApplication([])


def _region_item() -> tuple[RegionItem, QGraphicsScene]:
    item = RegionItem(
        "op-1",
        OperationType.MUTE,
        2.0,
        5.0,
        pixels_per_second=80.0,
        track_height=120.0,
        scroll_x=0.0,
        duration_s=20.0,
    )
    scene = QGraphicsScene()
    scene.addItem(item)
    return item, scene


def _item_hover(event_type: QEvent.Type, local: QPointF) -> QGraphicsSceneHoverEvent:
    event = QGraphicsSceneHoverEvent(event_type)
    event.setPos(local)
    return event


def _item_mouse(item: RegionItem, event_type: QEvent.Type, local: QPointF) -> QMouseEvent:
    buttons = (
        Qt.MouseButton.LeftButton
        if event_type != QEvent.Type.MouseButtonRelease
        else Qt.MouseButton.NoButton
    )
    return QMouseEvent(
        event_type,
        local,
        item.mapToScene(local),
        QPointF(),
        Qt.MouseButton.LeftButton,
        buttons,
        Qt.KeyboardModifier.NoModifier,
    )


def test_snap_time_uses_sample_rate() -> None:
    assert snap_time(0.00001, 48000) == 0.0
    assert snap_time(1.0 / 48000, 48000) == 1.0 / 48000


def test_snap_time_fine_uses_10ms() -> None:
    assert snap_time(0.014, 48000, fine=True) == 0.01


def test_playhead_snap_threshold_is_pixel_based() -> None:
    assert playhead_snap_threshold_s(80.0, pixels=10.0) == 0.125
    assert playhead_snap_threshold_s(10.0, pixels=10.0) == 1.0


def test_moved_range_snaps_end_to_playhead() -> None:
    start, end = snap_moved_range_to_playhead(
        5.05, 8.05, playhead_s=8.0, duration_s=20.0, threshold_s=0.125
    )
    assert end == pytest.approx(8.0)
    assert start == pytest.approx(5.0)


def test_moved_range_snaps_start_to_playhead() -> None:
    start, end = snap_moved_range_to_playhead(
        6.04, 9.04, playhead_s=6.0, duration_s=20.0, threshold_s=0.125
    )
    assert start == pytest.approx(6.0)
    assert end == pytest.approx(9.0)


def test_moved_range_prefers_end_when_both_edges_near() -> None:
    start, end = snap_moved_range_to_playhead(
        7.9, 8.04, playhead_s=8.0, duration_s=20.0, threshold_s=0.125
    )
    assert end == pytest.approx(8.0)
    assert start == pytest.approx(7.86)


def test_moved_range_does_not_snap_outside_threshold() -> None:
    start, end = snap_moved_range_to_playhead(
        4.0, 7.0, playhead_s=8.0, duration_s=20.0, threshold_s=0.125
    )
    assert (start, end) == (4.0, 7.0)


def test_moved_range_skips_snap_when_threshold_zero() -> None:
    start, end = snap_moved_range_to_playhead(
        5.05, 8.05, playhead_s=8.0, duration_s=20.0, threshold_s=0.0
    )
    assert (start, end) == (5.05, 8.05)


def test_resized_out_point_snaps_to_playhead() -> None:
    start, end = snap_resized_edge_to_playhead(
        2.0, 8.05, playhead_s=8.0, duration_s=20.0, threshold_s=0.125, edge="end"
    )
    assert start == 2.0
    assert end == pytest.approx(8.0)


def test_resized_in_point_snaps_to_playhead() -> None:
    start, end = snap_resized_edge_to_playhead(
        3.55, 5.0, playhead_s=3.5, duration_s=20.0, threshold_s=0.125, edge="start"
    )
    assert start == pytest.approx(3.5)
    assert end == 5.0


def test_resized_edge_does_not_snap_outside_threshold() -> None:
    start, end = snap_resized_edge_to_playhead(
        2.0, 7.0, playhead_s=8.0, duration_s=20.0, threshold_s=0.125, edge="end"
    )
    assert (start, end) == (2.0, 7.0)


def test_moved_range_snaps_end_to_other_region_start() -> None:
    start, end = snap_moved_range_to_targets(
        5.05, 8.05, (4.0, 8.0, 10.0), duration_s=20.0, threshold_s=0.125
    )
    assert end == pytest.approx(8.0)
    assert start == pytest.approx(5.0)


def test_moved_range_snaps_start_to_other_region_end() -> None:
    start, end = snap_moved_range_to_targets(
        4.04, 7.04, (2.0, 4.0, 10.0), duration_s=20.0, threshold_s=0.125
    )
    assert start == pytest.approx(4.0)
    assert end == pytest.approx(7.0)


def test_moved_range_prefers_closer_of_multiple_targets() -> None:
    start, end = snap_moved_range_to_targets(
        5.05, 8.05, (8.12, 8.02), duration_s=20.0, threshold_s=0.125
    )
    assert end == pytest.approx(8.02)
    assert start == pytest.approx(5.02)


def test_moved_range_ignores_targets_outside_threshold() -> None:
    start, end = snap_moved_range_to_targets(
        4.0, 7.0, (8.0, 10.0), duration_s=20.0, threshold_s=0.125
    )
    assert (start, end) == (4.0, 7.0)


def test_resized_out_point_snaps_to_other_region_edge() -> None:
    start, end = snap_resized_edge_to_targets(
        2.0, 8.05, (6.0, 8.0, 11.0), duration_s=20.0, threshold_s=0.125, edge="end"
    )
    assert start == 2.0
    assert end == pytest.approx(8.0)


def test_resized_in_point_snaps_to_other_region_edge() -> None:
    start, end = snap_resized_edge_to_targets(
        3.55, 5.0, (2.0, 3.5), duration_s=20.0, threshold_s=0.125, edge="start"
    )
    assert start == pytest.approx(3.5)
    assert end == 5.0


def test_resized_edge_prefers_closer_of_multiple_targets() -> None:
    start, end = snap_resized_edge_to_targets(
        2.0, 8.05, (8.12, 8.02), duration_s=20.0, threshold_s=0.125, edge="end"
    )
    assert end == pytest.approx(8.02)


def test_moved_range_finds_nearest_cut_in_a_long_sorted_list() -> None:
    targets = tuple(i * 0.5 for i in range(4000))
    start, end = snap_moved_range_to_targets(
        5.05, 8.05, targets, duration_s=3000.0, threshold_s=0.125
    )
    assert end == pytest.approx(8.0)
    assert start == pytest.approx(5.0)


def test_apply_move_preserves_duration_and_clamps_end() -> None:
    start, end = apply_move(10.0, 11.0, 95.0, duration_s=100.0)
    assert end == 100.0
    assert start == 99.0
    assert end - start == 1.0


def test_apply_resize_left_respects_min_width() -> None:
    start, end = apply_resize_left(1.0, 1.2, 0.5, duration_s=100.0)
    assert end - start >= 0.05


def test_apply_resize_right_clamps_to_duration() -> None:
    start, end = apply_resize_right(1.0, 2.0, 200.0, duration_s=10.0)
    assert end == 10.0


def test_nudge_range_moves_both_edges() -> None:
    start, end = nudge_range(1.0, 2.0, delta_start_s=0.5, delta_end_s=0.5, duration_s=100.0)
    assert start == 1.5
    assert end == 2.5


def test_region_hit_zones() -> None:
    assert RegionItem.hit_zone(2.0, 100.0) == "left"
    assert RegionItem.hit_zone(98.0, 100.0) == "right"
    assert RegionItem.hit_zone(50.0, 100.0) == "body"


def test_hit_zone_uses_x_relative_to_rect_not_item_origin() -> None:
    """Clicks must be measured from rect.left(); item pos.x() includes scene scroll offset."""
    rect_left = 500.0
    width = 100.0
    assert RegionItem.hit_zone(rect_left + 2.0 - rect_left, width) == "left"
    assert RegionItem.hit_zone(rect_left + 50.0 - rect_left, width) == "body"
    assert RegionItem.hit_zone(rect_left + 98.0 - rect_left, width) == "right"
    # Without subtracting rect_left, a body click would look like the right edge.
    assert RegionItem.hit_zone(rect_left + 50.0, width) == "right"


def test_interactive_hit_zone_body_when_unselected() -> None:
    region_rect = QRectF(0, 8, 120, 96)
    zone = RegionItem.interactive_hit_zone(
        20.0,
        40.0,
        region_rect,
        selected=False,
        gain_line_y=56.0,
        op_type=OperationType.VOLUME,
    )
    assert zone == "body"


def test_interactive_hit_zone_detects_gain_line_when_selected() -> None:
    region_rect = QRectF(0, 8, 120, 96)
    zone = RegionItem.interactive_hit_zone(
        60.0,
        56.0,
        region_rect,
        selected=True,
        gain_line_y=56.0,
        op_type=OperationType.VOLUME,
    )
    assert zone == "gain_level"


def test_interactive_hit_zone_prefers_trim_handles() -> None:
    region_rect = QRectF(0, 8, 120, 96)
    assert (
        RegionItem.interactive_hit_zone(
            2.0,
            40.0,
            region_rect,
            selected=True,
            gain_line_y=56.0,
            op_type=OperationType.VOLUME,
        )
        == "left"
    )


def test_interactive_hit_zone_ignores_handles_in_type_header() -> None:
    region_rect = QRectF(0, 8, 120, 96)
    header_h = RegionItem.header_height_for_rect(region_rect)
    zone = RegionItem.interactive_hit_zone(
        2.0,
        header_h - 1.0,
        region_rect,
        selected=True,
        gain_line_y=56.0,
        op_type=OperationType.VOLUME,
    )
    assert zone == "body"
    assert (
        RegionItem.interactive_hit_zone(
            2.0,
            header_h,
            region_rect,
            selected=True,
            gain_line_y=56.0,
            op_type=OperationType.VOLUME,
        )
        == "left"
    )


def test_trim_handles_fade_independently_on_hover() -> None:
    item, _scene = _region_item()
    body_y = item.rect().center().y()
    left = QPointF(item.rect().left() + 2.0, body_y)
    body = QPointF(item.rect().center())
    right = QPointF(item.rect().right() - 2.0, body_y)
    move = QEvent.Type.GraphicsSceneHoverMove

    item.hoverMoveEvent(_item_hover(move, left))
    assert item._handle_target_opacity("left") == 1.0
    assert item._handle_target_opacity("right") == 0.0

    item.hoverMoveEvent(_item_hover(move, body))
    assert item._handle_target_opacity("left") == 0.0
    assert item._handle_target_opacity("right") == 0.0

    item.hoverMoveEvent(_item_hover(move, right))
    assert item._handle_target_opacity("left") == 0.0
    assert item._handle_target_opacity("right") == 1.0

    header_h = RegionItem.header_height_for_rect(item.rect())
    item.hoverMoveEvent(
        _item_hover(move, QPointF(item.rect().left() + 2.0, item.rect().top() + header_h - 1.0))
    )
    assert item._handle_target_opacity("left") == 0.0
    assert item._handle_target_opacity("right") == 0.0


def test_disabled_region_press_is_select_only() -> None:
    item, _scene = _region_item()
    item.set_edit_enabled(False)
    left = QPointF(item.rect().left() + 2.0, item.rect().center().y())
    item.mousePressEvent(_item_mouse(item, QEvent.Type.MouseButtonPress, left))
    assert item.active_trim_endpoint() is None
    start = item.start_seconds()
    dragged = QPointF(item.rect().left() + 80.0, item.rect().center().y())
    item.mouseMoveEvent(_item_mouse(item, QEvent.Type.MouseMove, dragged))
    assert item.start_seconds() == start


def test_narrow_region_ignores_trim_and_drag() -> None:
    item = RegionItem(
        "op-tiny",
        OperationType.MUTE,
        2.0,
        5.0,
        pixels_per_second=2.0,
        track_height=120.0,
        scroll_x=0.0,
        duration_s=20.0,
    )
    scene = QGraphicsScene()
    scene.addItem(item)
    assert item.rect().width() < RegionItem.MIN_INTERACTIVE_WIDTH
    assert not item.geometry_edits_allowed()

    start, end = item.start_seconds(), item.end_seconds()
    left = QPointF(item.rect().left() + 1.0, item.rect().center().y())
    item.mousePressEvent(_item_mouse(item, QEvent.Type.MouseButtonPress, left))
    assert item.active_trim_endpoint() is None
    item.mouseMoveEvent(
        _item_mouse(
            item,
            QEvent.Type.MouseMove,
            QPointF(item.rect().left() + 40.0, item.rect().center().y()),
        )
    )
    assert item.start_seconds() == start
    assert item.end_seconds() == end
    item.mouseReleaseEvent(_item_mouse(item, QEvent.Type.MouseButtonRelease, left))

    center = item.rect().center()
    item.mousePressEvent(_item_mouse(item, QEvent.Type.MouseButtonPress, center))
    item.mouseMoveEvent(
        _item_mouse(item, QEvent.Type.MouseMove, QPointF(center.x() + 40.0, center.y()))
    )
    assert item.start_seconds() == start
    assert item.end_seconds() == end


def test_active_trim_endpoint_matches_handle() -> None:
    item, _scene = _region_item()
    left = QPointF(item.rect().left() + 2.0, item.rect().center().y())
    item.mousePressEvent(_item_mouse(item, QEvent.Type.MouseButtonPress, left))
    assert item.active_trim_endpoint() == "start"

    item.mouseReleaseEvent(_item_mouse(item, QEvent.Type.MouseButtonRelease, left))
    right = QPointF(item.rect().right() - 2.0, item.rect().center().y())
    item.mousePressEvent(_item_mouse(item, QEvent.Type.MouseButtonPress, right))
    assert item.active_trim_endpoint() == "end"


def test_active_trim_edge_follows_left_handle() -> None:
    item, _scene = _region_item()
    left = QPointF(item.rect().left() + 2.0, item.rect().center().y())
    item.mousePressEvent(_item_mouse(item, QEvent.Type.MouseButtonPress, left))
    assert item.active_trim_edge_seconds() == 2.0

    dragged = QPointF(item.rect().left() + 80.0, item.rect().center().y())
    item.mouseMoveEvent(_item_mouse(item, QEvent.Type.MouseMove, dragged))
    assert item.active_trim_edge_seconds() == item.start_seconds()
    assert item.start_seconds() > 2.0


def test_active_trim_edge_follows_right_handle() -> None:
    item, _scene = _region_item()
    right = QPointF(item.rect().right() - 2.0, item.rect().center().y())
    item.mousePressEvent(_item_mouse(item, QEvent.Type.MouseButtonPress, right))
    assert item.active_trim_edge_seconds() == 5.0

    dragged = QPointF(item.rect().right() + 80.0, item.rect().center().y())
    item.mouseMoveEvent(_item_mouse(item, QEvent.Type.MouseMove, dragged))
    assert item.active_trim_edge_seconds() == item.end_seconds()
    assert item.end_seconds() > 5.0


def test_active_trim_edge_none_for_body_drag() -> None:
    item, _scene = _region_item()
    body = QPointF(item.rect().center())
    item.mousePressEvent(_item_mouse(item, QEvent.Type.MouseButtonPress, body))
    assert item.active_trim_edge_seconds() is None
    item.mouseMoveEvent(
        _item_mouse(item, QEvent.Type.MouseMove, QPointF(body.x() + 40.0, body.y()))
    )
    assert item.active_trim_edge_seconds() is None


def test_selected_region_fill_is_more_opaque() -> None:
    item, _scene = _region_item()
    idle = item.brush().color().alpha()
    item.setSelected(True)
    selected = item.brush().color().alpha()
    assert selected > idle
    assert item.pen().widthF() == 2.0


def test_pose_markers_are_not_clickable() -> None:
    picked: list[tuple[str, int]] = []
    item = RegionItem(
        "op-cover",
        OperationType.COVER,
        10.0,
        17.0,
        pixels_per_second=80.0,
        track_height=120.0,
        scroll_x=0.0,
        duration_s=30.0,
        on_pose_selected=lambda op_id, index: picked.append((op_id, index)),
    )
    scene = QGraphicsScene()
    scene.addItem(item)
    item.set_pose_markers([13.0, 15.0], active_index=0)
    x = item._pose_item_x(15.0)
    assert x is not None
    item.mousePressEvent(
        _item_mouse(item, QEvent.Type.MouseButtonPress, QPointF(x, item.rect().center().y()))
    )
    assert picked == []


def test_trim_does_not_shift_pose_markers() -> None:
    item, _scene = _region_item()
    item.set_operation_type(OperationType.COVER)
    item.set_pose_markers([3.0, 4.0])
    right = QPointF(item.rect().right() - 2.0, item.rect().center().y())
    item.mousePressEvent(_item_mouse(item, QEvent.Type.MouseButtonPress, right))
    item.mouseMoveEvent(
        _item_mouse(
            item,
            QEvent.Type.MouseMove,
            QPointF(item.rect().right() + 80.0, item.rect().center().y()),
        )
    )
    assert item._pose_seconds == [3.0, 4.0]


def test_slide_does_not_shift_pose_markers() -> None:
    item, _scene = _region_item()
    item.set_operation_type(OperationType.COVER)
    item.set_pose_markers([3.0, 4.0])
    center = item.rect().center()
    item.mousePressEvent(_item_mouse(item, QEvent.Type.MouseButtonPress, center))
    item.mouseMoveEvent(
        _item_mouse(
            item,
            QEvent.Type.MouseMove,
            QPointF(center.x() + 80.0, center.y()),
        )
    )
    assert item._pose_seconds == [3.0, 4.0]


def test_pose_marker_outside_region_is_visible_not_clickable() -> None:
    picked: list[tuple[str, int]] = []
    item = RegionItem(
        "op-cover",
        OperationType.COVER,
        12.0,
        16.0,
        pixels_per_second=80.0,
        track_height=120.0,
        scroll_x=0.0,
        duration_s=30.0,
        on_pose_selected=lambda op_id, index: picked.append((op_id, index)),
    )
    scene = QGraphicsScene()
    scene.addItem(item)
    item.set_pose_markers([10.0, 12.0], active_index=1)
    x = item._pose_item_x(10.0)
    assert x is not None
    assert x < item.rect().left()
    bounds = item.boundingRect()
    assert bounds.left() <= x
    item.mousePressEvent(
        _item_mouse(item, QEvent.Type.MouseButtonPress, QPointF(x, item.rect().center().y()))
    )
    assert picked == []


def test_region_type_label_uses_display_name() -> None:
    assert region_type_label(OperationType.MUTE) == "Mute"
    assert region_type_label(OperationType.PIXELATE) == "Pixelate"


def test_region_body_path_is_rounded() -> None:
    item, _scene = _region_item()
    rect = item.rect()
    path = item._body_path(rect)
    bounds = path.boundingRect()
    assert abs(bounds.left() - rect.left()) < 0.01
    assert abs(bounds.right() - rect.right()) < 0.01
    assert item._corner_radius(rect) == RegionItem.CORNER_RADIUS
    # A sharp rect would contain its exact corner; the rounded path should not.
    assert not path.contains(rect.topLeft())
