"""Draggable edit region on the timeline."""

from __future__ import annotations

import contextlib
from collections.abc import Callable
from typing import Literal

from PySide6.QtCore import QAbstractAnimation, QEasingCurve, QPointF, QRectF, Qt, QVariantAnimation
from PySide6.QtGui import (
    QBrush,
    QColor,
    QFont,
    QFontMetrics,
    QGuiApplication,
    QPainter,
    QPainterPath,
    QPen,
)
from PySide6.QtWidgets import QGraphicsItem, QGraphicsRectItem, QStyleOptionGraphicsItem, QWidget
from shiboken6 import isValid

from corrcut.domain.operation import CensorParams, OperationType
from corrcut.ui.timeline.region_geometry import (
    MIN_REGION_DURATION_S,
    SortedSnapTargets,
    apply_move,
    apply_resize_left,
    apply_resize_right,
    playhead_snap_threshold_s,
    snap_moved_range_to_targets,
    snap_resized_edge_to_targets,
    snap_time,
    sorted_snap_targets,
)
from corrcut.ui.timeline.timeline_lanes import TimelineLane, lane_for_operation_type, lane_rect

GeometryChangedCallback = Callable[[str, float, float, bool], None]
ParamsChangedCallback = Callable[[str, CensorParams, bool], None]
PoseSelectedCallback = Callable[[str, int], None]
TrimHitZone = Literal["left", "right", "body"]
InteractiveHitZone = Literal["left", "right", "body", "gain_level"]
DragMode = Literal[
    "move",
    "resize_left",
    "resize_right",
    "gain_level",
    "body_undecided",
    "select_only",
]

_REGION_COLORS: dict[OperationType, QColor] = {
    OperationType.MUTE: QColor(220, 60, 60, 120),
    OperationType.VOLUME: QColor(220, 140, 40, 120),
    OperationType.BEEP: QColor(240, 200, 40, 120),
    OperationType.SKIP: QColor(120, 120, 140, 140),
    OperationType.BLANK: QColor(30, 30, 30, 160),
    OperationType.COVER: QColor(80, 80, 80, 140),
    OperationType.BLUR: QColor(100, 160, 220, 120),
    OperationType.PIXELATE: QColor(160, 100, 220, 120),
    OperationType.ZOOM: QColor(60, 180, 120, 120),
    OperationType.CROP: QColor(40, 140, 100, 120),
}


_REGION_TYPE_LABELS: dict[OperationType, str] = {
    OperationType.MUTE: "Mute",
    OperationType.VOLUME: "Volume",
    OperationType.BEEP: "Beep",
    OperationType.SKIP: "Skip",
    OperationType.BLANK: "Blank",
    OperationType.COVER: "Cover",
    OperationType.BLUR: "Blur",
    OperationType.PIXELATE: "Pixelate",
    OperationType.ZOOM: "Zoom",
    OperationType.CROP: "Crop",
}


def region_color(op_type: OperationType, *, alpha: int | None = None) -> QColor:
    color = QColor(_REGION_COLORS.get(op_type, QColor(180, 180, 180, 120)))
    if alpha is not None:
        color.setAlpha(alpha)
    return color


_BODY_ALPHA = 110
_BODY_SELECTED_ALPHA = 165
_BODY_SKIP_ALPHA = 70
_BODY_SKIP_SELECTED_ALPHA = 100
_HEADER_ALPHA = 235
_HEADER_SELECTED_ALPHA = 255
_HEADER_DISABLED_ALPHA = 120


def region_body_color(
    op_type: OperationType,
    *,
    enabled: bool = True,
    selected: bool = False,
    skip: bool = False,
    included: bool = True,
) -> QColor:
    color = region_color(op_type)
    if skip:
        color.setAlpha(_BODY_SKIP_SELECTED_ALPHA if selected else _BODY_SKIP_ALPHA)
    else:
        color.setAlpha(_BODY_SELECTED_ALPHA if selected else _BODY_ALPHA)
    if not enabled:
        color.setAlpha(max(40, int(color.alpha() * 0.4)))
    if not included:
        color.setAlpha(max(36, int(color.alpha() * 0.35)))
    return color


def region_header_color(
    op_type: OperationType,
    *,
    enabled: bool = True,
    selected: bool = False,
    included: bool = True,
) -> QColor:
    """Solid title-bar / outline color for a timeline region."""
    color = region_color(op_type)
    if not enabled:
        color.setAlpha(_HEADER_DISABLED_ALPHA)
    else:
        color.setAlpha(_HEADER_SELECTED_ALPHA if selected else _HEADER_ALPHA)
    if not included:
        color.setAlpha(max(36, int(color.alpha() * 0.35)))
    return color


def region_type_label(op_type: OperationType) -> str:
    return _REGION_TYPE_LABELS.get(op_type, op_type.value.replace("_", " ").title())


class RegionItem(QGraphicsRectItem):
    """Visual representation of an Operation time range."""

    HANDLE_WIDTH = 10.0
    HANDLE_FADE_MS = 200
    MIN_WIDTH = MIN_REGION_DURATION_S
    MIN_INLINE_WIDTH = 40.0
    MIN_INTERACTIVE_WIDTH = 7.0
    HEADER_HEIGHT = 16.0
    CORNER_RADIUS = 3.0
    GAIN_LINE_HIT = 8.0
    BODY_AXIS_THRESHOLD = 4.0
    POSE_HIT = 7.0

    def __init__(
        self,
        operation_id: str,
        op_type: OperationType,
        start_s: float,
        end_s: float,
        pixels_per_second: float,
        track_height: float,
        scroll_x: float,
        duration_s: float,
        sample_rate: int = 48000,
        censor_params: CensorParams | None = None,
        on_geometry_changed: GeometryChangedCallback | None = None,
        on_params_changed: ParamsChangedCallback | None = None,
        on_pose_selected: PoseSelectedCallback | None = None,
    ) -> None:
        super().__init__()
        self.operation_id = operation_id
        self.op_type = op_type
        self._params = censor_params or CensorParams.defaults()
        self._pps = pixels_per_second
        self._track_height = track_height
        self._scroll_x = scroll_x
        self._duration_s = max(0.001, duration_s)
        self._sample_rate = sample_rate
        self._start_s = start_s
        self._end_s = end_s
        self._on_geometry_changed = on_geometry_changed
        self._on_params_changed = on_params_changed
        self._on_pose_selected = on_pose_selected
        self._pose_seconds: list[float] = []
        self._active_pose = -1
        self._drag_mode: DragMode | None = None
        self._drag_anchor_s = 0.0
        self._snap_fine = False
        self._times_at_press: tuple[float, float] | None = None
        self._params_at_press: CensorParams | None = None
        self._body_press_local: QPointF | None = None
        self._chrome_edits_on_press = True
        self._edit_enabled = True
        self._playback_included = True
        self._hovered_handle: TrimHitZone | None = None
        self._pointer_down = False
        self._handle_opacity: dict[str, float] = {"left": 0.0, "right": 0.0}
        self._handle_fade: dict[str, QVariantAnimation | None] = {"left": None, "right": None}
        self._playhead_s = 0.0
        self._playhead_snap_s = 0.0
        self._magnetic_snap = True
        self._snap_to_scene_cuts = True
        self._scene_cut_times: tuple[float, ...] = ()
        # Built once per move/trim gesture. Scene cuts stay sorted for bisect.
        self._snap_target_cache: SortedSnapTargets | None = None
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable, True)
        self.setAcceptHoverEvents(True)
        self._apply_lane_z()
        self._apply_style()
        self._update_geometry()

    def time_range(self) -> tuple[float, float]:
        return self._start_s, self._end_s

    def start_seconds(self) -> float:
        return self._start_s

    def end_seconds(self) -> float:
        return self._end_s

    def active_trim_edge_seconds(self) -> float | None:
        """In or out time while dragging a handle; None if not trimming."""
        if self._drag_mode == "resize_left":
            return self._start_s
        if self._drag_mode == "resize_right":
            return self._end_s
        return None

    def active_trim_endpoint(self) -> str | None:
        """'start' or 'end' while dragging a handle; None if not trimming."""
        if self._drag_mode == "resize_left":
            return "start"
        if self._drag_mode == "resize_right":
            return "end"
        return None

    def censor_params(self) -> CensorParams:
        return self._params

    def set_time_range(self, start_s: float, end_s: float) -> None:
        self._start_s = start_s
        self._end_s = end_s
        self._update_geometry()

    def set_scroll_x(self, scroll_x: float) -> None:
        self._scroll_x = scroll_x
        self._update_geometry()

    def set_lane_height(self, lane_height: float) -> None:
        if lane_height == self._track_height:
            return
        self._track_height = lane_height
        self._update_geometry()

    def set_pixels_per_second(self, pps: float) -> None:
        self._pps = pps
        self._update_geometry()

    def set_duration_limit(self, duration_s: float) -> None:
        self._duration_s = max(0.001, duration_s)

    def set_sample_rate(self, sample_rate: int) -> None:
        self._sample_rate = sample_rate

    def set_playhead_seconds(self, seconds: float) -> None:
        self._playhead_s = float(seconds)

    def set_magnetic_snap_enabled(self, enabled: bool) -> None:
        self._magnetic_snap = bool(enabled)
        self._snap_target_cache = None

    def set_snap_to_scene_cuts_enabled(self, enabled: bool) -> None:
        self._snap_to_scene_cuts = bool(enabled)
        self._snap_target_cache = None
        if not self._snap_to_scene_cuts:
            self._scene_cut_times = ()

    def set_scene_cut_times(self, times: tuple[float, ...] | list[float]) -> None:
        self._snap_target_cache = None
        if not self._snap_to_scene_cuts:
            self._scene_cut_times = ()
            return
        self._scene_cut_times = tuple(float(t) for t in times)

    def _build_snap_targets(self) -> SortedSnapTargets:
        times = [self._playhead_snap_s]
        scene = self.scene()
        if scene is not None:
            for item in scene.items():
                if isinstance(item, RegionItem) and item is not self:
                    times.append(item.start_seconds())
                    times.append(item.end_seconds())
        if self._snap_to_scene_cuts and self._scene_cut_times:
            times.extend(self._scene_cut_times)
        return sorted_snap_targets(times)

    def _playhead_snap_threshold_s(self) -> float:
        if not self._magnetic_snap or self._snap_fine:
            return 0.0
        return playhead_snap_threshold_s(self._pps)

    def _magnetic_snap_targets_s(self) -> tuple[float, ...]:
        """Snap times for the current gesture.

        The scene walk and scene-cut copy happen once per drag. Later moves
        binary-search that sorted list.
        """
        snapping = self._drag_mode in ("move", "resize_left", "resize_right")
        if snapping and self._snap_target_cache is not None:
            return self._snap_target_cache
        targets = self._build_snap_targets()
        if snapping:
            self._snap_target_cache = targets
        return targets

    def set_view(
        self,
        *,
        pixels_per_second: float,
        scroll_x: float,
        track_height: float,
        duration_s: float,
    ) -> None:
        self._pps = pixels_per_second
        self._scroll_x = scroll_x
        self._track_height = track_height
        self._duration_s = max(0.001, duration_s)
        self._update_geometry()

    def lane(self) -> TimelineLane:
        return lane_for_operation_type(self.op_type)

    def _apply_lane_z(self, *, selected: bool | None = None) -> None:
        # Skip sits behind audio/video. Selected region rises so overlapping
        # same-lane ops can be edited (and repeated clicks can cycle the stack).
        if selected is None:
            selected = self.isSelected()
        if self.lane() is TimelineLane.SKIP:
            self.setZValue(2 if selected else 1)
        else:
            self.setZValue(15 if selected else 10)

    def set_operation_type(self, op_type: OperationType) -> None:
        self.op_type = op_type
        self._apply_lane_z()
        self._apply_style()
        self._update_geometry()
        self.update()

    def set_op_type(self, op_type: OperationType) -> None:
        self.set_operation_type(op_type)

    def set_censor_params(self, params: CensorParams) -> None:
        self._params = params
        self.update()

    def set_edit_enabled(self, enabled: bool) -> None:
        if self._edit_enabled == enabled:
            return
        self._edit_enabled = enabled
        self._apply_style()
        self.update()

    @property
    def edit_enabled(self) -> bool:
        return self._edit_enabled

    def set_playback_included(self, included: bool) -> None:
        """Dim a region the current playback profile would not apply. It stays editable."""
        included = bool(included)
        if self._playback_included == included:
            return
        self._playback_included = included
        self._apply_style()
        self.update()

    def playback_included(self) -> bool:
        return self._playback_included

    def set_pose_markers(self, seconds: list[float], active_index: int = -1) -> None:
        self.prepareGeometryChange()
        self._pose_seconds = [float(value) for value in seconds]
        self._active_pose = int(active_index)
        self.update()

    def set_active_pose(self, index: int) -> None:
        self._active_pose = int(index)
        self.update()

    def _pose_item_x(self, seconds: float) -> float | None:
        return float(seconds) * self._pps - self._scroll_x

    def boundingRect(self) -> QRectF:
        rect = super().boundingRect()
        if len(self._pose_seconds) < 2 or self._pps <= 0:
            return rect
        pad = 8.0
        xs = [self._pose_item_x(seconds) for seconds in self._pose_seconds]
        xs = [x for x in xs if x is not None]
        if not xs:
            return rect
        left = min(rect.left(), min(xs) - pad)
        right = max(rect.right(), max(xs) + pad)
        return QRectF(left, rect.top() - 1.0, right - left, rect.height() + 2.0)

    def shape(self):
        path = QPainterPath()
        path.addRect(self.rect())
        return path

    def geometry_edits_allowed(self) -> bool:
        """False when the region is too narrow to trim or drag safely (zoomed out)."""
        return self._edit_enabled and self.rect().width() >= self.MIN_INTERACTIVE_WIDTH

    @classmethod
    def hit_zone(cls, local_x: float, width: float) -> TrimHitZone:
        hw = min(cls.HANDLE_WIDTH, width / 3)
        if local_x <= hw:
            return "left"
        if local_x >= width - hw:
            return "right"
        return "body"

    @classmethod
    def interactive_hit_zone(
        cls,
        local_x: float,
        local_y: float,
        rect: QRectF,
        *,
        selected: bool,
        gain_line_y: float | None,
        op_type: OperationType,
        fade_in_px: float = 0.0,
        fade_out_px: float = 0.0,
        duck_line_y: float | None = None,
    ) -> InteractiveHitZone:
        # fade_* / duck_line_y kept for older call sites/tests.
        del fade_in_px, fade_out_px
        if duck_line_y is not None and gain_line_y is None:
            gain_line_y = duck_line_y
        width = rect.width()
        hw = min(cls.HANDLE_WIDTH, width / 3)
        # local_y is offset from rect.top() (see _local_y_in_rect).
        if local_y >= cls.header_height_for_rect(rect):
            if local_x <= hw:
                return "left"
            if local_x >= width - hw:
                return "right"
        if (
            selected
            and op_type == OperationType.VOLUME
            and gain_line_y is not None
            and abs(local_y - gain_line_y) <= cls.GAIN_LINE_HIT
        ):
            return "gain_level"
        return "body"

    def _region_duration_s(self) -> float:
        return max(self.MIN_WIDTH, self._end_s - self._start_s)

    def _gain_line_y(self, rect: QRectF) -> float | None:
        if self.op_type != OperationType.VOLUME:
            return None
        # gain 1.0 at top, 0.0 at bottom
        t = max(0.0, min(1.0, 1.0 - self._params.gain))
        return rect.top() + t * rect.height()

    def _gain_from_local_y(self, local_y: float, rect: QRectF) -> float:
        if rect.height() <= 0:
            return self._params.gain
        t = max(0.0, min(1.0, (local_y - rect.top()) / rect.height()))
        return max(0.0, min(1.0, 1.0 - t))

    def _scene_x_to_time(self, scene_x: float) -> float:
        t = (scene_x + self._scroll_x) / self._pps
        return max(0.0, min(self._duration_s, t))

    def _event_local_pos(self, event) -> QPointF:  # noqa: ANN001
        return event.position() if hasattr(event, "position") else event.pos()

    def _local_x_in_rect(self, event) -> float:  # noqa: ANN001
        return float(self._event_local_pos(event).x()) - self.rect().left()

    def _local_y_in_rect(self, event) -> float:  # noqa: ANN001
        return float(self._event_local_pos(event).y()) - self.rect().top()

    def _event_scene_x(self, event) -> float:  # noqa: ANN001
        return float(self.mapToScene(self._event_local_pos(event)).x())

    def _apply_style(self) -> None:
        selected = self.isSelected()
        skip = self.lane() is TimelineLane.SKIP
        self.setBrush(
            QBrush(
                region_body_color(
                    self.op_type,
                    enabled=self._edit_enabled,
                    selected=selected,
                    skip=skip,
                    included=self._playback_included,
                )
            )
        )
        outline = region_header_color(
            self.op_type,
            enabled=self._edit_enabled,
            selected=selected,
            included=self._playback_included,
        )
        pen = QPen(outline, 2 if selected else 1)
        if not self._edit_enabled:
            pen.setStyle(Qt.PenStyle.DashLine)
        self.setPen(pen)

    def itemChange(self, change, value):  # noqa: ANN001
        if change == QGraphicsItem.GraphicsItemChange.ItemSceneChange and value is None:
            self._stop_handle_fade()
        if change == QGraphicsItem.GraphicsItemChange.ItemSelectedHasChanged:
            self._apply_lane_z(selected=bool(value))
            self._apply_style()
            self.update()
        return super().itemChange(change, value)

    def _update_geometry(self) -> None:
        new_rect = lane_rect(
            self.lane(),
            self._start_s,
            self._end_s,
            self._pps,
            self._scroll_x,
            lane_height=self._track_height,
        )
        if self.rect() == new_rect:
            return
        self.prepareGeometryChange()
        self.setRect(new_rect)

    def _notify_geometry_changed(self, final: bool) -> None:
        if self._on_geometry_changed is not None:
            self._on_geometry_changed(self.operation_id, self._start_s, self._end_s, final)

    def _notify_params_changed(self, final: bool) -> None:
        if self._on_params_changed is not None:
            self._on_params_changed(self.operation_id, self._params, final)

    def _snap(self, t: float) -> float:
        return snap_time(t, self._sample_rate, fine=self._snap_fine)

    def _corner_radius(self, rect: QRectF) -> float:
        return min(self.CORNER_RADIUS, rect.width() / 4.0, rect.height() / 4.0)

    def _body_path(self, rect: QRectF) -> QPainterPath:
        path = QPainterPath()
        radius = self._corner_radius(rect)
        path.addRoundedRect(rect, radius, radius)
        return path

    def paint(
        self,
        painter: QPainter,
        option: QStyleOptionGraphicsItem,
        widget: QWidget | None = None,
    ) -> None:
        del option, widget
        rect = self.rect()
        path = self._body_path(rect)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setBrush(self.brush())
        painter.setPen(self.pen())
        painter.drawPath(path)
        if rect.width() < 4:
            return

        painter.save()
        painter.setClipPath(path)
        if self.lane() is TimelineLane.SKIP:
            self._paint_skip_hatch(painter, rect)
        handles = self._trim_handle_rects(rect)
        if self.geometry_edits_allowed() and handles is not None:
            base_alpha = 200 if self.isSelected() else 130
            painter.setPen(Qt.PenStyle.NoPen)
            for side, handle in zip(("left", "right"), handles, strict=True):
                opacity = self._handle_opacity[side]
                if opacity <= 0.01:
                    continue
                handle_alpha = round(base_alpha * opacity)
                painter.setBrush(QColor(255, 255, 255, max(0, min(255, handle_alpha))))
                painter.drawRect(handle)
        self._paint_type_header(painter, rect)

        if self._edit_enabled and self.isSelected() and rect.width() >= self.MIN_INLINE_WIDTH:
            if self.op_type == OperationType.VOLUME:
                self._paint_gain_level(painter, rect)
            if self.op_type == OperationType.BEEP and rect.width() >= 56:
                self._paint_beep_label(painter, rect)
        painter.restore()

        if self._edit_enabled:
            self._paint_pose_markers(painter, rect)

    def _paint_skip_hatch(self, painter: QPainter, rect: QRectF) -> None:
        painter.setPen(QPen(QColor(255, 255, 255, 50), 1))
        step = 10.0
        x = rect.left() + step
        while x < rect.right():
            painter.drawLine(int(x), int(rect.top()), int(x - step), int(rect.bottom()))
            x += step

    def _paint_gain_level(self, painter: QPainter, rect: QRectF) -> None:
        line_y = self._gain_line_y(rect)
        if line_y is None:
            return
        fill = QColor(0, 0, 0, 50)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(fill)
        painter.drawRect(QRectF(rect.left(), line_y, rect.width(), rect.bottom() - line_y))
        pen = QPen(QColor(255, 255, 255, 230), 2)
        painter.setPen(pen)
        painter.drawLine(rect.left(), line_y, rect.right(), line_y)

    def _paint_beep_label(self, painter: QPainter, rect: QRectF) -> None:
        painter.setPen(QPen(QColor(255, 255, 255, 230)))
        label = f"{int(self._params.beep_freq)} Hz"
        body = rect.adjusted(4, self._header_rect(rect).height(), -4, 0)
        painter.drawText(body, int(Qt.AlignmentFlag.AlignCenter), label)

    def _paint_pose_markers(self, painter: QPainter, rect: QRectF) -> None:
        if len(self._pose_seconds) < 2:
            return
        mid_y = rect.center().y()
        for index, seconds in enumerate(self._pose_seconds):
            x = self._pose_item_x(seconds)
            if x is None:
                continue
            outside = seconds < self._start_s - 1e-3 or seconds > self._end_s + 1e-3
            if outside:
                edge = rect.left() if seconds < self._start_s else rect.right()
                painter.setPen(QPen(QColor(220, 70, 70, 120), 1, Qt.PenStyle.DashLine))
                painter.drawLine(QPointF(edge, mid_y), QPointF(x, mid_y))
            active = index == self._active_pose
            radius = 5.0 if active else 3.5
            if outside:
                painter.setBrush(QColor(220, 50, 50, 240 if active else 200))
                painter.setPen(QPen(QColor(255, 160, 160, 255), 1.3 if active else 1.0))
            elif active:
                painter.setBrush(QColor(0, 220, 255, 255))
                painter.setPen(QPen(QColor(180, 245, 255, 255), 1.5))
            else:
                painter.setBrush(QColor(255, 255, 255, 230))
                painter.setPen(QPen(QColor(20, 24, 28, 200), 1))
            painter.drawEllipse(QPointF(x, mid_y), radius, radius)

    def _header_rect(self, rect: QRectF) -> QRectF:
        return QRectF(rect.left(), rect.top(), rect.width(), self.header_height_for_rect(rect))

    @classmethod
    def header_height_for_rect(cls, rect: QRectF) -> float:
        return min(cls.HEADER_HEIGHT, max(10.0, rect.height() * 0.28))

    def _paint_type_header(self, painter: QPainter, rect: QRectF) -> None:
        header = self._header_rect(rect)
        fill = region_header_color(
            self.op_type,
            enabled=self._edit_enabled,
            selected=self.isSelected(),
            included=self._playback_included,
        )
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(fill)
        painter.drawRect(header)
        if header.width() < 22:
            return
        font = QFont(painter.font())
        font.setPixelSize(10)
        font.setBold(True)
        painter.setFont(font)
        text_rect = header.adjusted(6, 0, -6, 0)
        metrics = QFontMetrics(font)
        label = metrics.elidedText(
            region_type_label(self.op_type),
            Qt.TextElideMode.ElideRight,
            max(1, int(text_rect.width())),
        )
        text_alpha = 245
        if not self._edit_enabled:
            text_alpha = 200
        elif not self._playback_included:
            text_alpha = 140
        painter.setPen(QColor(255, 255, 255, text_alpha))
        painter.drawText(
            text_rect,
            int(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft),
            label,
        )

    def _interactive_zone(self, event) -> InteractiveHitZone:  # noqa: ANN001
        rect = self.rect()
        return self.interactive_hit_zone(
            self._local_x_in_rect(event),
            self._local_y_in_rect(event),
            rect,
            selected=self.isSelected(),
            gain_line_y=self._gain_line_y(rect),
            op_type=self.op_type,
        )

    def _trim_handle_rects(self, rect: QRectF) -> tuple[QRectF, QRectF] | None:
        if rect.width() < 4:
            return None
        handle_top = self._header_rect(rect).bottom()
        handle_h = rect.bottom() - handle_top
        if handle_h < 1.0:
            return None
        hw = min(self.HANDLE_WIDTH, rect.width() / 3)
        return (
            QRectF(rect.left(), handle_top, hw, handle_h),
            QRectF(rect.right() - hw, handle_top, hw, handle_h),
        )

    def _invalidate_trim_handle(self, side: str) -> None:
        handles = self._trim_handle_rects(self.rect())
        if handles is None:
            self.update()
            return
        handle = handles[0] if side == "left" else handles[1]
        self.update(handle.adjusted(-2.0, -2.0, 2.0, 2.0))

    def _handle_target_opacity(self, side: str) -> float:
        if not self.geometry_edits_allowed():
            return 0.0
        dragging = self._drag_mode == ("resize_left" if side == "left" else "resize_right")
        if dragging or self._hovered_handle == side:
            return 1.0
        return 0.0

    def _sync_trim_handle_fade(self) -> None:
        for side in ("left", "right"):
            self._animate_handle_opacity(side, self._handle_target_opacity(side))

    def _animate_handle_opacity(self, side: str, target: float) -> None:
        anim = self._handle_fade[side]
        running = anim is not None and anim.state() == QAbstractAnimation.State.Running
        if running and float(anim.endValue()) == target:
            return
        if not running and abs(self._handle_opacity[side] - target) < 0.001:
            return
        anim = self._ensure_handle_fade(side)
        # stop()/setStartValue() emit the previous fade's values. A fade-out
        # was flashing fully transparent for a frame before easing back up.
        anim.blockSignals(True)
        anim.stop()
        anim.setStartValue(self._handle_opacity[side])
        anim.setEndValue(target)
        anim.setDuration(self.HANDLE_FADE_MS)
        anim.setEasingCurve(QEasingCurve.Type.InOutSine)
        anim.blockSignals(False)
        anim.start()

    def _ensure_handle_fade(self, side: str) -> QVariantAnimation:
        anim = self._handle_fade[side]
        if anim is not None:
            return anim
        anim = QVariantAnimation()
        anim.valueChanged.connect(lambda value, side=side: self._on_handle_fade_value(side, value))
        self._handle_fade[side] = anim
        return anim

    def _on_handle_fade_value(self, side: str, value: float) -> None:
        if not isValid(self):
            anim = self._handle_fade.get(side)
            if anim is not None:
                anim.stop()
            return
        self._handle_opacity[side] = float(value)
        self._invalidate_trim_handle(side)

    def _stop_handle_fade(self) -> None:
        for side in ("left", "right"):
            anim = self._handle_fade[side]
            self._handle_fade[side] = None
            if anim is None:
                continue
            anim.blockSignals(True)
            with contextlib.suppress(RuntimeError):
                anim.valueChanged.disconnect()
            anim.stop()
            anim.deleteLater()

    def _set_hovered_handle_from_event(self, event) -> None:  # noqa: ANN001
        if not self.geometry_edits_allowed():
            self._hovered_handle = None
            return
        zone = self._interactive_zone(event)
        self._hovered_handle = zone if zone in ("left", "right") else None

    def hoverEnterEvent(self, event) -> None:  # noqa: ANN001, N802
        self._set_hovered_handle_from_event(event)
        self._sync_trim_handle_fade()
        super().hoverEnterEvent(event)

    def hoverMoveEvent(self, event) -> None:  # noqa: ANN001, N802
        if not self.geometry_edits_allowed():
            self._hovered_handle = None
            self.setCursor(Qt.CursorShape.ArrowCursor)
            self._sync_trim_handle_fade()
            super().hoverMoveEvent(event)
            return
        zone = self._interactive_zone(event)
        hovered = zone if zone in ("left", "right") else None
        if hovered != self._hovered_handle:
            self._hovered_handle = hovered
            self._sync_trim_handle_fade()
        if zone in ("left", "right"):
            self.setCursor(Qt.CursorShape.SizeHorCursor)
        elif zone == "gain_level":
            self.setCursor(Qt.CursorShape.SizeVerCursor)
        else:
            self.setCursor(Qt.CursorShape.OpenHandCursor)
        super().hoverMoveEvent(event)

    def hoverLeaveEvent(self, event) -> None:  # noqa: ANN001, N802
        # A press grabs the mouse and Qt sends a leave. Clearing the hovered
        # handle then would fade it out for the whole drag.
        button_down = QGuiApplication.mouseButtons() != Qt.MouseButton.NoButton
        if self._pointer_down or button_down:
            super().hoverLeaveEvent(event)
            return
        self._hovered_handle = None
        self.unsetCursor()
        self._sync_trim_handle_fade()
        super().hoverLeaveEvent(event)

    def mousePressEvent(self, event) -> None:  # noqa: ANN001, N802
        self._pointer_down = True
        self._set_hovered_handle_from_event(event)
        self._sync_trim_handle_fade()
        if not self.geometry_edits_allowed():
            self._drag_mode = "select_only"
            event.accept()
            return
        zone = self._interactive_zone(event)
        self._snap_fine = bool(event.modifiers() & Qt.KeyboardModifier.ShiftModifier)
        self._times_at_press = (self._start_s, self._end_s)
        self._params_at_press = self._params
        self._body_press_local = self._event_local_pos(event)
        self._playhead_snap_s = self._playhead_s

        if not self._chrome_edits_on_press and zone == "gain_level":
            self._drag_mode = "select_only"
            event.accept()
            return

        if zone == "left":
            self._drag_mode = "resize_left"
        elif zone == "right":
            self._drag_mode = "resize_right"
        elif zone == "gain_level":
            self._drag_mode = "gain_level"
        else:
            self._drag_mode = "body_undecided"

        self._drag_anchor_s = self._scene_x_to_time(self._event_scene_x(event))
        if self._drag_mode in ("resize_left", "resize_right"):
            self.setCursor(Qt.CursorShape.SizeHorCursor)
        elif self._drag_mode == "gain_level":
            self.setCursor(Qt.CursorShape.SizeVerCursor)
        else:
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
        self._sync_trim_handle_fade()
        event.accept()

    def mouseMoveEvent(self, event) -> None:  # noqa: ANN001, N802
        if self._drag_mode is None or self._drag_mode == "select_only":
            return
        self._snap_fine = bool(event.modifiers() & Qt.KeyboardModifier.ShiftModifier)

        if self._drag_mode == "body_undecided" and self._body_press_local is not None:
            delta = self._event_local_pos(event) - self._body_press_local
            if abs(delta.y()) > self.BODY_AXIS_THRESHOLD and abs(delta.y()) >= abs(delta.x()):
                if self.op_type == OperationType.VOLUME:
                    self._drag_mode = "gain_level"
                    self.setCursor(Qt.CursorShape.SizeVerCursor)
                else:
                    self._drag_mode = "move"
                    self.setCursor(Qt.CursorShape.ClosedHandCursor)
            elif abs(delta.x()) > self.BODY_AXIS_THRESHOLD:
                self._drag_mode = "move"
                self.setCursor(Qt.CursorShape.ClosedHandCursor)

        if self._drag_mode == "gain_level":
            gain = self._gain_from_local_y(self._local_y_in_rect(event), self.rect())
            self._params = self._params.with_gain(gain)
            self.update()
            self._notify_params_changed(final=False)
        elif self._drag_mode == "move":
            current_s = self._snap(self._scene_x_to_time(self._event_scene_x(event)))
            origin = self._times_at_press or (self._start_s, self._end_s)
            # Total delta from press so snap is based on the mouse, not the
            # already-magnetized region (which would never unstick).
            unconstrained_start, unconstrained_end = apply_move(
                origin[0],
                origin[1],
                current_s - self._drag_anchor_s,
                self._duration_s,
                min_duration_s=self.MIN_WIDTH,
            )
            self._start_s, self._end_s = snap_moved_range_to_targets(
                unconstrained_start,
                unconstrained_end,
                self._magnetic_snap_targets_s(),
                self._duration_s,
                self._playhead_snap_threshold_s(),
                min_duration_s=self.MIN_WIDTH,
            )
            self._update_geometry()
            self._notify_geometry_changed(final=False)
        elif self._drag_mode in ("resize_left", "resize_right"):
            current_s = self._snap(self._scene_x_to_time(self._event_scene_x(event)))
            origin = self._times_at_press or (self._start_s, self._end_s)
            delta = current_s - self._drag_anchor_s
            if self._drag_mode == "resize_left":
                unconstrained_start, unconstrained_end = apply_resize_left(
                    origin[0],
                    origin[1],
                    delta,
                    self._duration_s,
                    min_duration_s=self.MIN_WIDTH,
                )
                edge = "start"
            else:
                unconstrained_start, unconstrained_end = apply_resize_right(
                    origin[0],
                    origin[1],
                    delta,
                    self._duration_s,
                    min_duration_s=self.MIN_WIDTH,
                )
                edge = "end"
            self._start_s, self._end_s = snap_resized_edge_to_targets(
                unconstrained_start,
                unconstrained_end,
                self._magnetic_snap_targets_s(),
                self._duration_s,
                self._playhead_snap_threshold_s(),
                edge=edge,
                min_duration_s=self.MIN_WIDTH,
            )
            self._update_geometry()
            self._notify_geometry_changed(final=False)

        event.accept()

    def mouseReleaseEvent(self, event) -> None:  # noqa: ANN001, N802
        if self._drag_mode is not None:
            if self._drag_mode == "gain_level":
                if self._params_at_press != self._params:
                    self._notify_params_changed(final=True)
            elif self._times_at_press is not None and (self._start_s, self._end_s) != self._times_at_press:
                self._notify_geometry_changed(final=True)
        self._drag_mode = None
        self._snap_target_cache = None
        self._pointer_down = False
        self._times_at_press = None
        self._params_at_press = None
        self._body_press_local = None
        if self.contains(self._event_local_pos(event)):
            self._set_hovered_handle_from_event(event)
        else:
            self._hovered_handle = None
        self.unsetCursor()
        self._sync_trim_handle_fade()
        event.accept()


# Backward-compatible alias for tests.
RegionItem.TrimHitZone = TrimHitZone  # type: ignore[attr-defined]
