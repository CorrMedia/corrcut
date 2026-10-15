"""Timeline with ruler, video lane, waveform, regions, playhead, and zoom."""

from __future__ import annotations

from PySide6.QtCore import QEvent, QPoint, QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QBrush, QColor, QKeyEvent, QMouseEvent, QPainter, QPen, QPolygonF
from PySide6.QtWidgets import (
    QFrame,
    QGraphicsItem,
    QGraphicsLineItem,
    QGraphicsRectItem,
    QGraphicsScene,
    QGraphicsView,
    QStyleOptionGraphicsItem,
    QWidget,
)

from corrcut.domain.ai_suggestion import AiSuggestion
from corrcut.domain.operation import CensorParams, Operation, OperationType, is_audio_operation
from corrcut.domain.spatial_keyframes import has_spatial_animation, spatial_keyframes
from corrcut.domain.timeline_flag import TimelineFlag
from corrcut.engine.waveform import PeakStore
from corrcut.preview.playback_profile import PlaybackProfile
from corrcut.ui.flag_slots import FlagSlotStyle, resolve_flag_color
from corrcut.ui.timeline.filmstrip_item import VideoFilmstripItem
from corrcut.ui.timeline.flag_marker_item import FlagMarkerItem
from corrcut.ui.timeline.region_geometry import MIN_REGION_DURATION_S, nudge_range
from corrcut.ui.timeline.region_item import RegionItem, region_color
from corrcut.ui.timeline.suggestion_placeholder_item import (
    SuggestionPlaceholderItem,
    suggestion_edit_label,
)
from corrcut.ui.timeline.time_ruler_item import TimeRulerItem
from corrcut.ui.timeline.timeline_lanes import (
    AUDIO_LANE_HEIGHT,
    LANE_HEIGHT,
    MAX_LANE_HEIGHT,
    MIN_LANE_HEIGHT,
    RULER_HEIGHT,
    TRACK_GUTTER,
    VIDEO_TOP,
    TimelineLane,
    audio_top_for,
    gutter_top_for,
    is_ruler_y,
    is_track_gutter_y,
    lane_at_y,
    lane_for_operation_type,
    lane_height_for_viewport,
    lane_rect,
    scene_height_for,
)
from corrcut.ui.timeline.timeline_mode import TimelineMode
from corrcut.ui.timeline.timeline_navigator import NAVIGATOR_HEIGHT, TimelineNavigator
from corrcut.ui.timeline.waveform_item import WaveformItem
from corrcut.ui.timeline.zoom_mapping import clamp_pixels_per_second

# Downward half-diamond sitting on the ruler baseline (flat top, tip on the line).
_PLAYHEAD_HEAD_HALF_W = 5.0
_PLAYHEAD_HEAD_HEIGHT = 7.0
_PLAYHEAD_COLOR = QColor(255, 72, 72)


def playhead_draw_x(scene_x: float) -> float:
    """Snap to a pixel center so the 1px line and triangle stay symmetric."""
    return round(scene_x) + 0.5


_FLAG_DRAG_THRESHOLD_PX = 4.0
_EMPTY_DRAW_THRESHOLD_PX = 6.0


def playhead_head_polygon(
    x: float,
    baseline_y: float,
    *,
    half_w: float = _PLAYHEAD_HEAD_HALF_W,
    height: float = _PLAYHEAD_HEAD_HEIGHT,
) -> QPolygonF:
    return QPolygonF(
        [
            QPointF(x - half_w, baseline_y - height),
            QPointF(x + half_w, baseline_y - height),
            QPointF(x, baseline_y),
        ]
    )


DRAG_AUTOSCROLL_EDGE_PX = 28.0
DRAG_AUTOSCROLL_PX_PER_TICK = 20.0
DRAG_AUTOSCROLL_TICK_MS = 16


def drag_autoscroll_delta(
    view_x: float,
    viewport_width: float,
    *,
    edge_px: float = DRAG_AUTOSCROLL_EDGE_PX,
    px_per_tick: float = DRAG_AUTOSCROLL_PX_PER_TICK,
) -> float:
    """Scroll delta for one edge tick. Positive = later time (right)."""
    if viewport_width <= 1:
        return 0.0
    edge = min(edge_px, viewport_width * 0.2)
    if edge <= 0:
        return 0.0
    if view_x >= viewport_width - edge:
        strength = (view_x - (viewport_width - edge)) / edge
        return px_per_tick * min(3.0, max(0.0, strength))
    if view_x <= edge:
        strength = (edge - view_x) / edge
        return -px_per_tick * min(3.0, max(0.0, strength))
    return 0.0


class PlayheadHeadItem(QGraphicsItem):
    """Half-diamond cap; paints with AA so the edges are not stair-stepped."""

    def __init__(self) -> None:
        super().__init__()
        self._polygon = QPolygonF()
        self._color = _PLAYHEAD_COLOR
        self.setZValue(21)
        self.setAcceptedMouseButtons(Qt.MouseButton.NoButton)

    def set_polygon(self, polygon: QPolygonF) -> None:
        if polygon == self._polygon:
            return
        self.prepareGeometryChange()
        self._polygon = QPolygonF(polygon)
        self.update()

    def polygon(self) -> QPolygonF:
        return QPolygonF(self._polygon)

    def boundingRect(self) -> QRectF:  # noqa: N802
        if self._polygon.isEmpty():
            return QRectF()
        return self._polygon.boundingRect().adjusted(-1.0, -1.0, 1.0, 1.0)

    def paint(  # noqa: N802
        self,
        painter: QPainter,
        option: QStyleOptionGraphicsItem,
        widget: QWidget | None = None,
    ) -> None:
        del option, widget
        if self._polygon.isEmpty():
            return
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(self._color)
        painter.drawPolygon(self._polygon)


class TimelineWidget(QGraphicsView):
    playhead_changed = Signal(float)
    playhead_scrub_started = Signal()
    playhead_scrub_finished = Signal(float)
    # Preview-only seek while trimming in/out; timeline playhead stays put for magnet snap.
    trim_preview_seek = Signal(float)
    trim_preview_finished = Signal(float)
    operation_created = Signal(str, float, float)  # type_name, start_s, end_s
    operation_moved = Signal(str, float, float, bool)  # op_id, start_s, end_s, final
    operation_params_changed = Signal(str, object, bool)  # op_id, CensorParams, final
    operation_selected = Signal(str)
    operation_deselected = Signal()
    pose_selected = Signal(str, int)
    operation_delete_requested = Signal(str)
    operation_toggle_enabled_requested = Signal(str)
    draw_operation_type_changed = Signal(str)
    timeline_mode_changed = Signal(str)
    zoom_changed = Signal(float)
    filmstrip_tiles_needed = Signal(object)  # (visible times, prefetch times)
    flag_selected = Signal(str)
    flag_moved = Signal(str, float, bool)  # flag_id, seconds, final
    flag_delete_requested = Signal(str)
    suggestion_accept_requested = Signal(str)
    suggestion_reject_requested = Signal(str)
    flag_context_menu_requested = Signal(str, object)
    # Empty timeline: duration was resized to the visible width.
    placeholder_span_changed = Signal(float)
    # Scrollbar / navigator panning settled — deferred progressive peak paint.
    scroll_settled = Signal()

    # Backward-compatible alias (preferred audio lane height).
    TRACK_HEIGHT = AUDIO_LANE_HEIGHT

    @staticmethod
    def height_for_lanes(lane_height: float) -> int:
        """Widget height for a shared lane size, including the overview bar."""
        return int(scene_height_for(lane_height)) + NAVIGATOR_HEIGHT + 2

    def __init__(self) -> None:
        super().__init__()
        self._scene = QGraphicsScene(self)
        self.setScene(self._scene)
        self.setRenderHint(self.renderHints().Antialiasing, False)
        self.setViewportUpdateMode(QGraphicsView.ViewportUpdateMode.MinimalViewportUpdate)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        # The default sunken frame insets the lanes, so the tracks stop short of both edges.
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
        self.setBackgroundBrush(QBrush(QColor(28, 28, 30)))
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self._lane_height = LANE_HEIGHT
        self.setMinimumHeight(self.height_for_lanes(MIN_LANE_HEIGHT))
        self.setMaximumHeight(self.height_for_lanes(MAX_LANE_HEIGHT))

        self._duration = 1.0
        self._pps = 80.0
        self._laid_out_width = -1
        self._scroll_x = 0.0
        self._playhead_s = 0.0
        self._sample_rate = 48000
        self._edits_enabled = True
        self._playback_edits_on = True
        self._playback_profile = PlaybackProfile.full()
        self._synced_operations: list[Operation] = []
        self._magnetic_snap = True
        self._snap_to_scene_cuts = True
        self._scene_cut_times: tuple[float, ...] = ()
        self._mode = TimelineMode.SELECT
        self._draw_operation_type = OperationType.MUTE
        self._audio_draw_type = OperationType.MUTE
        self._video_draw_type = OperationType.COVER
        self._right_drag_op_type: OperationType | None = None
        self._empty_drag_start_s: float | None = None
        self._empty_drag_current_s: float | None = None
        self._empty_drag_scrub = False
        self._empty_drag_kind: str | None = None
        self._empty_drag_op_type: OperationType | None = None
        self._empty_press_view_x = 0.0
        self._right_drag_start_s: float | None = None
        self._right_drag_current_s: float | None = None
        self._draw_preview: QGraphicsRectItem | None = None
        self._active_region: RegionItem | None = None
        self._active_flag: FlagMarkerItem | None = None
        self._flag_did_drag = False
        self._flag_press_view_x = 0.0
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.NoContextMenu)
        self.viewport().setContextMenuPolicy(Qt.ContextMenuPolicy.NoContextMenu)

        self._ruler = TimeRulerItem()
        self._scene.addItem(self._ruler)

        self._video_bg = QGraphicsRectItem()
        self._video_bg.setBrush(QBrush(QColor(36, 36, 42)))
        self._video_bg.setPen(QPen(Qt.PenStyle.NoPen))
        self._video_bg.setZValue(-2)
        self._video_bg.setAcceptedMouseButtons(Qt.MouseButton.NoButton)
        self._scene.addItem(self._video_bg)

        self._track_gutter = QGraphicsRectItem()
        self._track_gutter.setBrush(QBrush(QColor(10, 10, 12)))
        self._track_gutter.setPen(QPen(Qt.PenStyle.NoPen))
        self._track_gutter.setZValue(-2)
        self._track_gutter.setAcceptedMouseButtons(Qt.MouseButton.NoButton)
        self._scene.addItem(self._track_gutter)

        self._filmstrip = VideoFilmstripItem(self._lane_height)
        self._filmstrip.setPos(0, VIDEO_TOP)
        self._filmstrip.on_missing_tiles = self._queue_filmstrip_tiles
        self._scene.addItem(self._filmstrip)

        self._waveform = WaveformItem(self._lane_height)
        self._waveform.setPos(0, audio_top_for(self._lane_height))
        self._waveform.setAcceptedMouseButtons(Qt.MouseButton.NoButton)
        self._scene.addItem(self._waveform)

        self._playhead_line = QGraphicsLineItem()
        line_pen = QPen(_PLAYHEAD_COLOR, 1)
        line_pen.setCapStyle(Qt.PenCapStyle.FlatCap)
        line_pen.setCosmetic(True)
        self._playhead_line.setPen(line_pen)
        self._playhead_line.setZValue(20)
        self._playhead_line.setAcceptedMouseButtons(Qt.MouseButton.NoButton)
        self._scene.addItem(self._playhead_line)
        self._playhead_head = PlayheadHeadItem()
        self._scene.addItem(self._playhead_head)

        self._navigator = TimelineNavigator(self)
        self._navigator.scroll_requested.connect(self._on_navigator_scroll)
        self.setViewportMargins(0, 0, 0, NAVIGATOR_HEIGHT)

        self._regions: dict[str, RegionItem] = {}
        self._suggestion_placeholders: dict[str, SuggestionPlaceholderItem] = {}
        self._flags: dict[str, FlagMarkerItem] = {}
        self._peak_store: PeakStore | None = None
        self._suppress_scroll_signal = False
        self._resize_in_progress = False
        self._batch_old_pps = self._pps
        self._viewport_mode_before_resize = self.viewportUpdateMode()
        self._resize_finalize_timer = QTimer(self)
        self._resize_finalize_timer.setSingleShot(True)
        self._resize_finalize_timer.setInterval(16)
        self._resize_finalize_timer.timeout.connect(self._finalize_resize)

        self._filmstrip_request_timer = QTimer(self)
        self._filmstrip_request_timer.setSingleShot(True)
        self._filmstrip_request_timer.setInterval(200)
        self._filmstrip_request_timer.timeout.connect(self._flush_filmstrip_request)
        self._pending_filmstrip_visible: list[float] = []
        self._pending_filmstrip_prefetch: list[float] = []

        self._drag_autoscroll_timer = QTimer(self)
        self._drag_autoscroll_timer.setInterval(DRAG_AUTOSCROLL_TICK_MS)
        self._drag_autoscroll_timer.timeout.connect(self._on_drag_autoscroll_tick)
        self._drag_autoscroll_view_pos = QPointF()

        self._scroll_interacting = False
        self._scroll_settle_timer = QTimer(self)
        self._scroll_settle_timer.setSingleShot(True)
        self._scroll_settle_timer.setInterval(80)
        self._scroll_settle_timer.timeout.connect(self._end_scroll_interaction)
        bar = self.horizontalScrollBar()
        bar.sliderPressed.connect(self._begin_scroll_interaction)
        bar.sliderReleased.connect(self._end_scroll_interaction)

        self.horizontalScrollBar().valueChanged.connect(self._on_scroll)

    def _viewport_width(self) -> float:
        return max(1.0, float(self.viewport().width()))

    def _content_width(self) -> float:
        return self._duration * self._pps

    def _scene_x_to_time(self, scene_x: float) -> float:
        return max(0.0, min(self._duration, (scene_x + self._scroll_x) / self._pps))

    def _clamp_view_x(self, view_x: float) -> float:
        return max(0.0, min(view_x, self._viewport_width()))

    def _view_x_to_time(self, view_x: float) -> float:
        return self._scene_x_to_time(self._clamp_view_x(view_x))

    def _time_to_scene_x(self, time_s: float) -> float:
        return time_s * self._pps - self._scroll_x

    def _sync_lane_height(self) -> bool:
        """Shrink both lanes together when the timeline is shorter than the preferred scene."""
        height = lane_height_for_viewport(float(self.viewport().height()))
        if abs(height - self._lane_height) < 0.25:
            return False
        self._lane_height = height
        self._filmstrip.set_lane_height(height)
        self._waveform.set_lane_height(height)
        for region in self._regions.values():
            region.set_lane_height(height)
        for placeholder in self._suggestion_placeholders.values():
            placeholder.set_lane_height(height)
        return True

    def _apply_lane_geometry(self, vw: float) -> None:
        lane = self._lane_height
        bar = self.horizontalScrollBar()
        bar.blockSignals(True)
        try:
            # A new scene rect resets the horizontal bar; keep the saved scroll.
            self._scene.setSceneRect(0, 0, vw, scene_height_for(lane))
        finally:
            bar.blockSignals(False)
        self._video_bg.setRect(0, VIDEO_TOP, vw, lane)
        self._track_gutter.setRect(0, gutter_top_for(lane), vw, TRACK_GUTTER)
        self._filmstrip.setPos(0, VIDEO_TOP)
        self._waveform.setPos(0, audio_top_for(lane))

    def _clamp_scroll_x(self) -> None:
        """Keep scroll inside the media. Negative scroll draws a gap before time 0."""
        content = int(self._content_width())
        viewport = int(self._viewport_width())
        max_scroll = max(0, content - viewport)
        self._scroll_x = float(int(max(0.0, min(self._scroll_x, float(max_scroll)))))

    def _apply_view(self) -> None:
        # Zoom anchoring can request scroll before time 0. Clamp before the lanes
        # are positioned so the ruler, media, and click mapping stay on the same origin.
        self._clamp_scroll_x()
        vw = self._viewport_width()
        self._laid_out_width = int(vw)
        self._apply_lane_geometry(vw)
        self._ruler.set_view_state(self._scroll_x, vw, self._pps, self._duration)
        self._ruler.set_scene_cuts(
            self._scene_cut_times if self._snap_to_scene_cuts else (),
        )
        self._filmstrip.set_view_state(self._scroll_x, vw, self._pps, self._duration)
        self._waveform.set_view_state(self._scroll_x, vw, self._pps, self._duration)
        self._sync_track_items_layout()
        self._update_playhead()
        self._refresh_draw_preview()
        self._update_scrollbar()
        self._sync_navigator()

    def _apply_resize_layout(self, *, pps_changed: bool) -> None:
        """Layout sync after dock width settles."""
        self._clamp_scroll_x()
        vw = self._viewport_width()
        self._laid_out_width = int(vw)
        self._apply_lane_geometry(vw)
        self._ruler.set_view_state(
            self._scroll_x,
            vw,
            self._pps,
            self._duration,
            repaint=False,
        )
        self._filmstrip.set_view_state(
            self._scroll_x,
            vw,
            self._pps,
            self._duration,
            repaint=False,
        )
        self._waveform.set_view_state(
            self._scroll_x,
            vw,
            self._pps,
            self._duration,
            repaint=False,
        )
        self._update_scrollbar()
        self._update_playhead()
        if pps_changed:
            self._sync_track_items_layout()
            self._refresh_draw_preview()
            self._ruler.set_view_state(self._scroll_x, vw, self._pps, self._duration)
            self._filmstrip.set_view_state(self._scroll_x, vw, self._pps, self._duration)
            self._waveform.set_view_state(self._scroll_x, vw, self._pps, self._duration)

    def _sync_scrollbar_only(self) -> None:
        """Restore virtual scroll after Qt resets the bar during resize."""
        bar = self.horizontalScrollBar()
        content = int(self._content_width())
        viewport = int(self._viewport_width())
        max_scroll = max(0, content - viewport)
        clamped = int(max(0, min(self._scroll_x, float(max_scroll))))
        bar.blockSignals(True)
        if bar.maximum() != max_scroll:
            bar.setRange(0, max_scroll)
        if bar.value() != clamped:
            bar.setValue(clamped)
        self._scroll_x = float(clamped)
        bar.blockSignals(False)
        self._pin_view_origin()

    def _pin_view_origin(self) -> None:
        """Drop a leftover graphics-view translate so view x=0 stays scene x=0.

        Horizontal scroll is virtual. Qt still bakes the scrollbar into the view
        on resize or scene-rect changes, and the corrective scroll is ignored.
        Clicks then land on a later timecode than the pixels under the cursor.
        """
        drift = int(round(self.mapToScene(QPoint(0, 0)).x()))
        if drift == 0:
            return
        bar = self.horizontalScrollBar()
        value = bar.value()
        bar.blockSignals(True)
        try:
            QGraphicsView.scrollContentsBy(self, drift, 0)
            if bar.value() != value:
                bar.setValue(value)
        finally:
            bar.blockSignals(False)

    def _begin_resize_interaction(self) -> None:
        self._resize_in_progress = True
        self._viewport_mode_before_resize = self.viewportUpdateMode()
        self.setViewportUpdateMode(QGraphicsView.ViewportUpdateMode.NoViewportUpdate)
        self._filmstrip.set_fast_resize_preview(True)
        self._waveform.set_fast_resize_preview(True)

    def _end_resize_interaction(self) -> None:
        # Restore updates before asking items to repaint. Calls made while the
        # view is frozen are dropped, and the layout pass already stored the
        # new size, so a later sync sees nothing to redraw.
        self.setViewportUpdateMode(self._viewport_mode_before_resize)
        self._resize_in_progress = False
        self._filmstrip.set_fast_resize_preview(False)
        self._waveform.set_fast_resize_preview(False)
        self._sync_viewport_items(repaint=True)
        self.viewport().update()
        self._filmstrip.request_missing_tiles()

    def _finalize_resize(self) -> None:
        if not self._resize_in_progress:
            return
        old_pps = self._batch_old_pps
        pps_changed = False
        clamped = clamp_pixels_per_second(self._pps, self._viewport_width())
        if clamped != old_pps:
            pps_changed = True
            if old_pps > 0:
                time_at_left = self._scroll_x / old_pps
                self._scroll_x = time_at_left * clamped
            self._pps = clamped
        self._apply_resize_layout(pps_changed=pps_changed)
        self._end_resize_interaction()
        if pps_changed:
            self.zoom_changed.emit(self._pps)

    def _update_scrollbar(self) -> None:
        bar = self.horizontalScrollBar()
        content = int(self._content_width())
        viewport = int(self._viewport_width())
        max_scroll = max(0, content - viewport)
        bar.blockSignals(True)
        if bar.maximum() != max_scroll:
            bar.setRange(0, max_scroll)
        if bar.pageStep() != viewport:
            bar.setPageStep(viewport)
        single = max(1, viewport // 20)
        if bar.singleStep() != single:
            bar.setSingleStep(single)
        clamped = int(max(0, min(self._scroll_x, float(max_scroll))))
        if bar.value() != clamped:
            bar.setValue(clamped)
        self._scroll_x = float(clamped)
        bar.blockSignals(False)
        self._pin_view_origin()

    def _layout_navigator(self) -> None:
        if getattr(self, "_navigator", None) is None:
            return
        self.setViewportMargins(0, 0, 0, NAVIGATOR_HEIGHT)
        self._navigator.setGeometry(
            0, self.height() - NAVIGATOR_HEIGHT, self.width(), NAVIGATOR_HEIGHT
        )

    def _sync_navigator(self) -> None:
        if getattr(self, "_navigator", None) is None:
            return
        self._navigator.set_view_state(
            scroll_x=self._scroll_x,
            viewport_width=self._viewport_width(),
            pps=self._pps,
            duration=self._duration,
            playhead_s=self._playhead_s,
        )
        self._navigator.set_flags(
            [(item.seconds(), item.color()) for item in getattr(self, "_flags", {}).values()]
        )

    def _on_navigator_scroll(self, scroll_x: float) -> None:
        self._begin_scroll_interaction()
        max_scroll = max(0.0, self._content_width() - self._viewport_width())
        self.horizontalScrollBar().setValue(int(round(max(0.0, min(max_scroll, scroll_x)))))
        self._scroll_settle_timer.start()

    def _on_scroll(self, value: int) -> None:
        if self._suppress_scroll_signal:
            return
        self._begin_scroll_interaction()
        self._scroll_x = float(value)
        self._sync_viewport_items()
        self._sync_track_items_scroll()
        self._update_playhead()
        self._refresh_draw_preview()
        self._sync_navigator()
        # Wheel / programmatic scroll: settle shortly after the last movement.
        if not self.horizontalScrollBar().isSliderDown():
            self._scroll_settle_timer.start()

    def is_scroll_interacting(self) -> bool:
        return bool(self._scroll_interacting)

    def _begin_scroll_interaction(self) -> None:
        self._scroll_interacting = True

    def _end_scroll_interaction(self) -> None:
        if not self._scroll_interacting:
            return
        self._scroll_interacting = False
        self._scroll_settle_timer.stop()
        self.scroll_settled.emit()

    def refresh_peak_display(self) -> None:
        self._apply_view()
        self._waveform.refresh_peaks()

    def refresh_peaks_only(self) -> None:
        """Cheap progressive-build update — skip a full view sync."""
        self._waveform.refresh_peaks()

    def scrollContentsBy(self, dx: int, dy: int) -> None:  # noqa: N802
        if dx != 0:
            return
        super().scrollContentsBy(0, dy)

    def showEvent(self, event) -> None:  # noqa: ANN001, N802
        super().showEvent(event)
        # The viewport often reaches its real size on show, after the first layout pass.
        lanes_changed = self._sync_lane_height()
        if not self._edits_enabled:
            self.fill_visible_span()
            self._layout_navigator()
            return
        if lanes_changed or int(self._viewport_width()) != self._laid_out_width:
            self._apply_view()
            self._layout_navigator()

    def resizeEvent(self, event) -> None:  # noqa: ANN001, N802
        if not self._edits_enabled:
            super().resizeEvent(event)
            self._sync_lane_height()
            self._layout_navigator()
            self.fill_visible_span()
            return
        saved_scroll = self._scroll_x
        bar = self.horizontalScrollBar()
        self._suppress_scroll_signal = True
        bar.blockSignals(True)
        try:
            super().resizeEvent(event)
            self._scroll_x = saved_scroll
            self._sync_scrollbar_only()
        finally:
            bar.blockSignals(False)
            self._suppress_scroll_signal = False

        self._layout_navigator()
        lanes_changed = self._sync_lane_height()
        # Qt has usually already resized the viewport before this runs, so compare
        # against the width the lanes were last drawn at — not the width at entry.
        if int(self._viewport_width()) == self._laid_out_width:
            if lanes_changed:
                self._apply_lane_geometry(self._viewport_width())
                # Changing the scene rect resets the horizontal bar.
                self._sync_scrollbar_only()
                self._update_playhead()
                self._refresh_draw_preview()
            else:
                self._sync_navigator()
            return

        if lanes_changed:
            self._apply_lane_geometry(self._viewport_width())
            self._sync_scrollbar_only()
            self._update_playhead()

        if not self._resize_in_progress:
            self._batch_old_pps = self._pps
            self._begin_resize_interaction()
        self._resize_finalize_timer.start()

    def edits_enabled(self) -> bool:
        return self._edits_enabled

    def set_edits_enabled(self, enabled: bool) -> None:
        """Block drawing edits on the placeholder timeline until media is loaded."""
        enabled = bool(enabled)
        changed = self._edits_enabled != enabled
        self._edits_enabled = enabled
        if changed:
            self._navigator.set_interactive(enabled)
            if not enabled:
                self._resize_finalize_timer.stop()
                if self._resize_in_progress:
                    self._end_resize_interaction()
                self._clear_empty_drag()
                self._clear_right_drag()
        if not enabled:
            self.fill_visible_span()

    def set_playback_filter(self, *, edits_on: bool, profile: PlaybackProfile) -> None:
        """Dim regions Edited preview would skip. Authoring stays available."""
        edits_on = bool(edits_on)
        if self._playback_edits_on == edits_on and self._playback_profile == profile:
            return
        self._playback_edits_on = edits_on
        self._playback_profile = profile
        self._apply_playback_filter()

    def _playback_includes(self, op: Operation) -> bool:
        if not self._playback_edits_on or not op.enabled:
            return True
        return self._playback_profile.matches(op)

    def _apply_playback_filter(self) -> None:
        by_id = {op.id: op for op in self._synced_operations}
        for op_id, item in self._regions.items():
            op = by_id.get(op_id)
            item.set_playback_included(True if op is None else self._playback_includes(op))

    def fill_visible_span(self) -> float:
        """Make an empty timeline only as long as the current viewport."""
        pps = max(self._pps, 1e-6)
        seconds = max(0.001, self._viewport_width() / pps)
        changed = abs(seconds - self._duration) > 1e-4
        self._scroll_x = 0.0
        self._duration = seconds
        if self._playhead_s > seconds:
            self._playhead_s = seconds
        self._apply_view()
        if changed:
            self.placeholder_span_changed.emit(seconds)
        return seconds

    def set_duration(self, seconds: float) -> None:
        self._duration = max(0.001, seconds)
        self._apply_view()

    def set_sample_rate(self, sample_rate: int) -> None:
        self._sample_rate = sample_rate or 48000
        for region in self._regions.values():
            region.set_sample_rate(self._sample_rate)

    def set_pixels_per_second(
        self,
        pps: float,
        *,
        anchor_view_x: float | None = None,
    ) -> None:
        old_pps = self._pps
        new_pps = clamp_pixels_per_second(pps, self._viewport_width())
        if anchor_view_x is not None and old_pps > 0:
            time_at_anchor = (self._scroll_x + anchor_view_x) / old_pps
            self._scroll_x = time_at_anchor * new_pps - anchor_view_x
        self._pps = new_pps
        self._apply_view()
        self.zoom_changed.emit(self._pps)

    def fit_to_viewport(self) -> None:
        if self._duration <= 0:
            return
        pps = self._viewport_width() / self._duration
        self._scroll_x = 0.0
        self.set_pixels_per_second(pps)

    def playhead_viewport_x(self) -> float:
        return self._time_to_scene_x(self._playhead_s)

    def pixels_per_second(self) -> float:
        return self._pps

    def magnetic_snap_enabled(self) -> bool:
        return self._magnetic_snap

    def set_magnetic_snap_enabled(self, enabled: bool) -> None:
        enabled = bool(enabled)
        if self._magnetic_snap == enabled:
            return
        self._magnetic_snap = enabled
        for region in self._regions.values():
            region.set_magnetic_snap_enabled(enabled)

    def snap_to_scene_cuts_enabled(self) -> bool:
        return self._snap_to_scene_cuts

    def set_snap_to_scene_cuts_enabled(self, enabled: bool) -> None:
        enabled = bool(enabled)
        self._snap_to_scene_cuts = enabled
        # Always push to regions so a toggle never leaves stale cut magnets.
        times = self._scene_cut_times if enabled else ()
        for region in self._regions.values():
            region.set_snap_to_scene_cuts_enabled(enabled)
            region.set_scene_cut_times(times)
        self._ruler.set_scene_cuts(times)
        self._ruler.update()

    def scene_cut_times(self) -> tuple[float, ...]:
        return self._scene_cut_times

    def set_scene_cut_times(self, times: tuple[float, ...] | list[float]) -> None:
        normalized = tuple(float(t) for t in times)
        self._scene_cut_times = normalized
        active = normalized if self._snap_to_scene_cuts else ()
        for region in self._regions.values():
            region.set_snap_to_scene_cuts_enabled(self._snap_to_scene_cuts)
            region.set_scene_cut_times(active)
        self._ruler.set_scene_cuts(active)
        self._ruler.update()

    def timeline_mode(self) -> TimelineMode:
        return self._mode

    def set_timeline_mode(self, mode: TimelineMode) -> None:
        if self._mode == mode:
            return
        self._mode = mode
        self._clear_empty_drag()
        self._clear_right_drag()
        self.timeline_mode_changed.emit(mode.value)

    def set_draw_operation_type(self, op_type: OperationType) -> None:
        if op_type == OperationType.SKIP:
            pass
        elif is_audio_operation(op_type):
            self._audio_draw_type = op_type
        else:
            self._video_draw_type = op_type
        if self._draw_operation_type == op_type:
            return
        self._draw_operation_type = op_type
        self.draw_operation_type_changed.emit(op_type.value)
        if self._draw_preview is not None:
            self._style_draw_preview(op_type)

    def _draw_type_for_lane(self, lane: TimelineLane | None) -> OperationType:
        if self._draw_operation_type == OperationType.SKIP:
            return OperationType.SKIP
        if lane is TimelineLane.VIDEO:
            return self._video_draw_type
        return self._audio_draw_type

    def set_active_tool(self, tool: OperationType) -> None:
        """Legacy hook: remember the last draw type for that family."""
        self.set_draw_operation_type(tool)

    def set_peak_store(self, store: PeakStore | None, duration: float) -> None:
        self._peak_store = store
        self._duration = max(0.001, duration)
        self._waveform.set_peak_store(store)
        self._apply_view()

    def set_filmstrip_source(
        self,
        *,
        tile_width: int,
        tile_height: int,
        aspect: float | None = None,
    ) -> None:
        self._filmstrip.reset()
        self._filmstrip.set_tile_size(tile_width, tile_height, aspect=aspect)
        self._filmstrip.set_enabled(True)
        self._apply_view()

    def clear_filmstrip(self) -> None:
        self._filmstrip_request_timer.stop()
        self._pending_filmstrip_visible = []
        self._pending_filmstrip_prefetch = []
        self._filmstrip.reset()

    def filmstrip_has_holes(self) -> bool:
        return self._filmstrip.has_unfilled_tiles()

    def reopen_filmstrip_holes(self) -> None:
        self._filmstrip.reopen_filmstrip_holes()

    def ingest_filmstrip_frames(
        self,
        frames: object,
        failed: object = (),
    ) -> None:
        frame_list = list(frames) if frames else []
        failed_list = list(failed) if failed else []
        self._filmstrip.ingest_frames(frame_list, failed_list)

    def _queue_filmstrip_tiles(self, visible: list[float], prefetch: list[float]) -> None:
        if self._resize_in_progress or (not visible and not prefetch):
            return
        if (
            visible == self._pending_filmstrip_visible
            and prefetch == self._pending_filmstrip_prefetch
            and self._filmstrip_request_timer.isActive()
        ):
            return
        self._pending_filmstrip_visible = list(visible)
        self._pending_filmstrip_prefetch = list(prefetch)
        self._filmstrip_request_timer.start()

    def _flush_filmstrip_request(self) -> None:
        visible = self._pending_filmstrip_visible
        prefetch = self._pending_filmstrip_prefetch
        self._pending_filmstrip_visible = []
        self._pending_filmstrip_prefetch = []
        if visible or prefetch:
            self.filmstrip_tiles_needed.emit((visible, prefetch))

    def set_playhead(self, seconds: float, *, notify: bool = False) -> None:
        self._playhead_s = max(0.0, min(self._duration, seconds))
        self._update_playhead()
        if notify:
            self.playhead_changed.emit(self._playhead_s)

    def go_to_time(self, seconds: float, *, scroll_into_view: bool = True) -> None:
        self.set_playhead(seconds, notify=True)
        if scroll_into_view:
            self.scroll_playhead_into_view()

    def _sync_viewport_items(self, *, repaint: bool = True) -> None:
        vw = self._viewport_width()
        self._ruler.set_view_state(self._scroll_x, vw, self._pps, self._duration, repaint=repaint)
        self._filmstrip.set_view_state(
            self._scroll_x, vw, self._pps, self._duration, repaint=repaint
        )
        self._waveform.set_view_state(
            self._scroll_x, vw, self._pps, self._duration, repaint=repaint
        )

    def go_to_start(self, *, notify: bool = False) -> None:
        """Move playhead to 0 and align scroll to the project start (left edge)."""
        self._scroll_x = 0.0
        self.set_playhead(0.0, notify=notify)
        bar = self.horizontalScrollBar()
        bar.blockSignals(True)
        bar.setValue(0)
        bar.blockSignals(False)
        self._sync_viewport_items()
        self._sync_track_items_scroll()
        self._update_playhead()

    def scroll_playhead_into_view(self) -> None:
        vw = self._viewport_width()
        content = self._content_width()
        target = self._playhead_s * self._pps - vw / 2.0
        max_scroll = max(0.0, content - vw)
        self._scroll_x = max(0.0, min(target, max_scroll))
        bar = self.horizontalScrollBar()
        bar.blockSignals(True)
        bar.setValue(int(self._scroll_x))
        bar.blockSignals(False)
        self._sync_viewport_items()
        self._sync_track_items_scroll()
        self._update_playhead()

    def scroll_playhead_keep_visible(self, *, margin_ratio: float = 0.15) -> None:
        """Auto-scroll minimally so the playhead stays visible.

        Unlike scroll_playhead_into_view(), this does not constantly center the playhead.
        """
        vw = self._viewport_width()
        if vw <= 1:
            return
        # At project start there is no scroll room to the left; keep scroll pinned at 0
        # instead of trying to push the playhead into the left margin band.
        if self._playhead_s <= 0.0:
            if abs(self._scroll_x) < 0.5:
                return
            self._scroll_x = 0.0
            bar = self.horizontalScrollBar()
            bar.blockSignals(True)
            bar.setValue(0)
            bar.blockSignals(False)
            self._sync_viewport_items()
            self._sync_track_items_scroll()
            self._update_playhead()
            return

        content = self._content_width()
        max_scroll = max(0.0, content - vw)

        margin_ratio = max(0.0, min(0.45, float(margin_ratio)))
        margin = vw * margin_ratio
        left_limit = margin
        right_limit = vw - margin

        playhead_x = self._time_to_scene_x(self._playhead_s)
        new_scroll = self._scroll_x
        if playhead_x < left_limit:
            new_scroll = self._playhead_s * self._pps - left_limit
        elif playhead_x > right_limit:
            new_scroll = self._playhead_s * self._pps - right_limit

        new_scroll = max(0.0, min(new_scroll, max_scroll))
        if abs(new_scroll - self._scroll_x) < 0.5:
            return

        self._scroll_x = new_scroll
        bar = self.horizontalScrollBar()
        bar.blockSignals(True)
        bar.setValue(int(self._scroll_x))
        bar.blockSignals(False)

        self._sync_viewport_items()
        self._sync_track_items_scroll()
        self._update_playhead()

    def playhead_seconds(self) -> float:
        return self._playhead_s

    def _drag_is_active(self) -> bool:
        return (
            self._active_region is not None
            or (self._active_flag is not None and self._flag_did_drag)
            or self._empty_drag_kind in ("scrub", "create")
            or self._right_drag_start_s is not None
        )

    def _nudge_scroll_x(self, delta: float) -> bool:
        vw = self._viewport_width()
        max_scroll = max(0.0, self._content_width() - vw)
        new_scroll = max(0.0, min(self._scroll_x + delta, max_scroll))
        if abs(new_scroll - self._scroll_x) < 0.5:
            return False
        self._scroll_x = new_scroll
        bar = self.horizontalScrollBar()
        bar.blockSignals(True)
        bar.setValue(int(self._scroll_x))
        bar.blockSignals(False)
        self._sync_viewport_items()
        self._sync_track_items_scroll()
        self._update_playhead()
        self._refresh_draw_preview()
        return True

    def _tick_drag_autoscroll(self) -> bool:
        if not self._drag_is_active():
            self._stop_drag_autoscroll_timer()
            return False
        delta = drag_autoscroll_delta(self._drag_autoscroll_view_pos.x(), self._viewport_width())
        if delta == 0.0:
            self._stop_drag_autoscroll_timer()
            return False
        moved = self._nudge_scroll_x(delta)
        if not self._drag_autoscroll_timer.isActive():
            self._drag_autoscroll_timer.start()
        return moved

    def _stop_drag_autoscroll_timer(self) -> None:
        if self._drag_autoscroll_timer.isActive():
            self._drag_autoscroll_timer.stop()

    def _on_drag_autoscroll_tick(self) -> None:
        if not self._drag_is_active():
            self._stop_drag_autoscroll_timer()
            return
        self._tick_drag_autoscroll()
        self._apply_drag_follow_from_view_pos(self._drag_autoscroll_view_pos)

    def _apply_drag_follow_from_view_pos(self, pos: QPointF) -> None:
        t = self._view_x_to_time(pos.x())
        if self._active_region is not None:
            scene_pos = QPointF(self._clamp_view_x(pos.x()), pos.y())
            local = self._active_region.mapFromScene(scene_pos)
            move = QMouseEvent(
                QEvent.Type.MouseMove,
                local,
                scene_pos,
                QPointF(),
                Qt.MouseButton.LeftButton,
                Qt.MouseButton.LeftButton,
                Qt.KeyboardModifier.NoModifier,
            )
            self._active_region.mouseMoveEvent(move)
            self._emit_trim_preview(self._active_region)
            return
        if self._active_flag is not None:
            self._move_active_flag_from_view_x(pos.x())
            return
        if self._empty_drag_start_s is not None:
            self._empty_drag_current_s = t
            if (
                self._empty_drag_kind == "undecided"
                and abs(pos.x() - self._empty_press_view_x) >= _EMPTY_DRAW_THRESHOLD_PX
            ):
                self._empty_drag_kind = "create"
            if self._empty_drag_kind == "scrub":
                self.set_playhead(t, notify=True)
            elif self._empty_drag_kind == "create":
                self._refresh_draw_preview()
            return
        if self._right_drag_start_s is not None:
            self._right_drag_current_s = t
            self._refresh_draw_preview()

    def selected_operation_id(self) -> str | None:
        for item in self._regions.values():
            if item.isSelected():
                return item.operation_id
        return None

    def selected_flag_id(self) -> str | None:
        for item in self._flags.values():
            if item.isSelected():
                return item.flag_id
        return None

    def selected_suggestion_id(self) -> str | None:
        for item in self._suggestion_placeholders.values():
            if item.isSelected():
                return item.suggestion_id
        return None

    def select_operation(self, op_id: str) -> None:
        self.clear_flag_selection()
        self.clear_suggestion_selection()
        for item in self._regions.values():
            item.setSelected(item.operation_id == op_id)

    def select_flag(self, flag_id: str) -> None:
        if self.clear_operation_selection():
            self.operation_deselected.emit()
        self.clear_suggestion_selection()
        for item in self._flags.values():
            item.setSelected(item.flag_id == flag_id)

    def select_suggestion(self, suggestion_id: str) -> None:
        if suggestion_id not in self._suggestion_placeholders:
            return
        if self.clear_operation_selection():
            self.operation_deselected.emit()
        self.clear_flag_selection()
        for item in self._suggestion_placeholders.values():
            item.setSelected(item.suggestion_id == suggestion_id)

    def clear_suggestion_selection(self) -> bool:
        if self.selected_suggestion_id() is None:
            return False
        for item in self._suggestion_placeholders.values():
            item.setSelected(False)
        return True

    def clear_flag_selection(self) -> bool:
        if self.selected_flag_id() is None:
            return False
        for item in self._flags.values():
            item.setSelected(False)
        return True

    def nudge_selected_region(
        self,
        direction: int,
        *,
        fine: bool = False,
        trim_in: bool = False,
        trim_out: bool = False,
    ) -> bool:
        """Slide or trim the selected region. Returns True if a region was nudged."""
        if direction == 0:
            return False
        op_id = self.selected_operation_id()
        if op_id is None or op_id not in self._regions:
            return False
        item = self._regions[op_id]
        if not item.edit_enabled:
            return False
        sign = -1.0 if direction < 0 else 1.0
        step = sign * self._nudge_step_s(fine=fine)
        start_s, end_s = item.time_range()
        preview_edge_s: float | None = None
        if trim_in and not trim_out:
            new_start, new_end = nudge_range(
                start_s,
                end_s,
                delta_start_s=step,
                duration_s=self._duration,
            )
            preview_edge_s = new_start
        elif trim_out and not trim_in:
            new_start, new_end = nudge_range(
                start_s,
                end_s,
                delta_end_s=step,
                duration_s=self._duration,
            )
            preview_edge_s = new_end
        else:
            new_start, new_end = nudge_range(
                start_s,
                end_s,
                delta_start_s=step,
                delta_end_s=step,
                duration_s=self._duration,
            )
        if (new_start, new_end) == (start_s, end_s):
            return True
        item.set_time_range(new_start, new_end)
        self._on_region_geometry(op_id, new_start, new_end, final=True)
        if preview_edge_s is not None:
            # Preview the trimmed edge; keep the playhead parked for magnet snap.
            self.playhead_scrub_started.emit()
            self.trim_preview_finished.emit(float(preview_edge_s))
        return True

    def update_operation_params(
        self,
        op_id: str,
        op_type: OperationType,
        params: CensorParams,
    ) -> None:
        item = self._regions.get(op_id)
        if item is None:
            return
        item.set_op_type(op_type)
        item.set_censor_params(params)

    def clear_operation_selection(self) -> bool:
        """Deselect all regions. Returns True if a region was selected."""
        if self.selected_operation_id() is None:
            return False
        for item in self._regions.values():
            item.setSelected(False)
        return True

    def sync_flags(
        self,
        flags: list[TimelineFlag],
        styles: dict[int, FlagSlotStyle],
    ) -> None:
        current_ids = {flag.id for flag in flags}
        for flag_id in list(self._flags.keys()):
            if flag_id not in current_ids:
                item = self._flags.pop(flag_id)
                self._scene.removeItem(item)

        for flag in flags:
            style = styles.get(flag.slot)
            slot_color = style.color if style is not None else QColor(180, 180, 180)
            color = resolve_flag_color(flag.color, slot_color)
            label = style.display_label() if style is not None else f"Flag {flag.slot}"
            if flag.id in self._flags:
                item = self._flags[flag.id]
                item.set_seconds(flag.seconds)
                item.set_slot(flag.slot)
                item.set_appearance(color, label)
            else:
                item = FlagMarkerItem(
                    flag.id,
                    flag.seconds,
                    flag.slot,
                    color,
                    label,
                    self._pps,
                    self._scroll_x,
                )
                self._scene.addItem(item)
                self._flags[flag.id] = item
        self._sync_navigator()

    def sync_operations(self, operations: list[Operation]) -> None:
        self._synced_operations = list(operations)
        current_ids = {o.id for o in operations}
        for op_id in list(self._regions.keys()):
            if op_id not in current_ids:
                item = self._regions.pop(op_id)
                self._scene.removeItem(item)

        for op in operations:
            start_s = float(op.time_range.start.seconds)
            end_s = float(op.time_range.end.seconds)
            if op.id in self._regions:
                item = self._regions[op.id]
                item.set_op_type(op.type)
                item.set_censor_params(op.censor_params())
                item.set_time_range(start_s, end_s)
                item.set_edit_enabled(op.enabled)
                item.set_playback_included(self._playback_includes(op))
                item.set_magnetic_snap_enabled(self._magnetic_snap)
                item.set_snap_to_scene_cuts_enabled(self._snap_to_scene_cuts)
                item.set_scene_cut_times(self._scene_cut_times if self._snap_to_scene_cuts else ())
            else:
                item = RegionItem(
                    op.id,
                    op.type,
                    start_s,
                    end_s,
                    self._pps,
                    self._lane_height,
                    self._scroll_x,
                    self._duration,
                    self._sample_rate,
                    censor_params=op.censor_params(),
                    on_geometry_changed=self._on_region_geometry,
                    on_params_changed=self._on_region_params,
                    on_pose_selected=self._on_pose_selected,
                )
                self._scene.addItem(item)
                self._regions[op.id] = item
                item.set_edit_enabled(op.enabled)
                item.set_playback_included(self._playback_includes(op))
                item.set_playhead_seconds(self._playhead_s)
                item.set_magnetic_snap_enabled(self._magnetic_snap)
                item.set_snap_to_scene_cuts_enabled(self._snap_to_scene_cuts)
                item.set_scene_cut_times(self._scene_cut_times if self._snap_to_scene_cuts else ())
            if has_spatial_animation(op):
                item.set_pose_markers([kf.seconds for kf in spatial_keyframes(op)])
            else:
                item.set_pose_markers([])

    def sync_suggestions(self, suggestions: list[AiSuggestion]) -> None:
        current_ids = {s.id for s in suggestions}
        for suggestion_id in list(self._suggestion_placeholders.keys()):
            if suggestion_id not in current_ids:
                item = self._suggestion_placeholders.pop(suggestion_id)
                self._scene.removeItem(item)

        for suggestion in suggestions:
            start_s = float(suggestion.time_range.start.seconds)
            end_s = float(suggestion.time_range.end.seconds)
            label = suggestion_edit_label(suggestion)
            if suggestion.id in self._suggestion_placeholders:
                item = self._suggestion_placeholders[suggestion.id]
                item.set_time_range(start_s, end_s)
                item.set_label(label)
            else:
                item = SuggestionPlaceholderItem(
                    suggestion.id,
                    start_s,
                    end_s,
                    self._pps,
                    self._lane_height,
                    self._scroll_x,
                    self._duration,
                    label=label,
                )
                self._scene.addItem(item)
                self._suggestion_placeholders[suggestion.id] = item

    def _sync_track_items_layout(self) -> None:
        for region in self._regions.values():
            region.set_scroll_x(self._scroll_x)
            region.set_pixels_per_second(self._pps)
            region.set_duration_limit(self._duration)
        for placeholder in self._suggestion_placeholders.values():
            placeholder.set_scroll_x(self._scroll_x)
            placeholder.set_pixels_per_second(self._pps)
            placeholder.set_duration_limit(self._duration)
        for flag in self._flags.values():
            flag.set_scroll_x(self._scroll_x)
            flag.set_pixels_per_second(self._pps)

    def _sync_track_items_scroll(self) -> None:
        for region in self._regions.values():
            region.set_scroll_x(self._scroll_x)
        for placeholder in self._suggestion_placeholders.values():
            placeholder.set_scroll_x(self._scroll_x)
        for flag in self._flags.values():
            flag.set_scroll_x(self._scroll_x)

    def _on_region_geometry(self, op_id: str, start_s: float, end_s: float, final: bool) -> None:
        self.operation_moved.emit(op_id, start_s, end_s, final)

    def _on_pose_selected(self, op_id: str, index: int) -> None:
        self.select_operation(op_id)
        self.pose_selected.emit(op_id, index)

    def set_active_pose(self, op_id: str | None, index: int) -> None:
        for item_id, item in self._regions.items():
            item.set_active_pose(index if item_id == op_id else -1)

    def sync_pose_markers(self, op: Operation, active_index: int = -1) -> None:
        item = self._regions.get(op.id)
        if item is None:
            return
        if has_spatial_animation(op):
            item.set_pose_markers([kf.seconds for kf in spatial_keyframes(op)], active_index)
        else:
            item.set_pose_markers([])

    def active_trim_endpoint(self) -> str | None:
        region = self._active_region
        if region is None:
            op_id = self.selected_operation_id()
            region = self._regions.get(op_id) if op_id else None
        if region is None:
            return None
        return region.active_trim_endpoint()

    def _emit_trim_preview(self, region: RegionItem) -> float | None:
        """Seek preview to the trim edge without moving the timeline playhead."""
        edge = region.active_trim_edge_seconds()
        if edge is None:
            return None
        self.trim_preview_seek.emit(float(edge))
        return float(edge)

    def _on_region_params(self, op_id: str, params: CensorParams, final: bool) -> None:
        self.operation_params_changed.emit(op_id, params, final)

    def _update_playhead(self) -> None:
        x = playhead_draw_x(self._time_to_scene_x(self._playhead_s))
        # Line starts at the ruler baseline so it does not cut through timecode labels.
        self._playhead_line.setLine(x, RULER_HEIGHT, x, scene_height_for(self._lane_height))
        self._playhead_head.set_polygon(playhead_head_polygon(x, RULER_HEIGHT))
        for region in self._regions.values():
            region.set_playhead_seconds(self._playhead_s)
        self._sync_navigator()

    def _clear_empty_drag(self) -> None:
        self._empty_drag_start_s = None
        self._empty_drag_current_s = None
        self._empty_drag_scrub = False
        self._empty_drag_kind = None
        self._empty_drag_op_type = None
        if self._right_drag_start_s is None:
            self._clear_draw_preview_item()
        if not self._drag_is_active():
            self._stop_drag_autoscroll_timer()

    def _clear_right_drag(self) -> None:
        self._right_drag_start_s = None
        self._right_drag_current_s = None
        self._right_drag_op_type = None
        if self._empty_drag_start_s is None or self._empty_drag_scrub:
            self._clear_draw_preview_item()
        if not self._drag_is_active():
            self._stop_drag_autoscroll_timer()

    def _clear_draw_preview_item(self) -> None:
        if self._draw_preview is not None:
            self._scene.removeItem(self._draw_preview)
            self._draw_preview = None

    def _rect_for_time_range(self, start_s: float, end_s: float, op_type: OperationType) -> QRectF:
        return lane_rect(
            lane_for_operation_type(op_type),
            start_s,
            end_s,
            self._pps,
            self._scroll_x,
            lane_height=self._lane_height,
        )

    def _style_draw_preview(self, op_type: OperationType) -> None:
        if self._draw_preview is None:
            return
        fill = region_color(op_type, alpha=90)
        self._draw_preview.setBrush(QBrush(fill))
        self._draw_preview.setPen(QPen(QColor(255, 255, 255, 200), 1, Qt.PenStyle.DashLine))

    def _draw_drag_bounds(self) -> tuple[float, float, OperationType] | None:
        if self._right_drag_start_s is not None and self._right_drag_current_s is not None:
            return (
                self._right_drag_start_s,
                self._right_drag_current_s,
                self._right_drag_op_type or self._draw_operation_type,
            )
        if (
            self._empty_drag_start_s is not None
            and self._empty_drag_current_s is not None
            and self._empty_drag_kind == "create"
            and self._empty_drag_op_type is not None
        ):
            return self._empty_drag_start_s, self._empty_drag_current_s, self._empty_drag_op_type
        return None

    def _refresh_draw_preview(self) -> None:
        bounds = self._draw_drag_bounds()
        if bounds is None:
            return
        start_s, current_s, op_type = bounds
        if self._draw_preview is None:
            self._draw_preview = QGraphicsRectItem()
            self._draw_preview.setZValue(5)
            self._scene.addItem(self._draw_preview)
        self._style_draw_preview(op_type)
        self._draw_preview.setRect(self._rect_for_time_range(start_s, current_s, op_type))

    def _emit_region_created(self, start_s: float, end_s: float, op_type: OperationType) -> bool:
        lo = min(start_s, end_s)
        hi = max(start_s, end_s)
        if hi - lo >= MIN_REGION_DURATION_S:
            self.operation_created.emit(op_type.value, lo, hi)
            return True
        return False

    def _forward_mouse_to_region(self, region: RegionItem, event: QMouseEvent) -> None:
        scene_pos = self.mapToScene(event.position().toPoint())
        scene_pos.setX(self._clamp_view_x(float(scene_pos.x())))
        local = region.mapFromScene(scene_pos)
        global_pos = event.globalPosition()
        forwarded = QMouseEvent(
            event.type(),
            local,
            scene_pos,
            global_pos,
            event.button(),
            event.buttons(),
            event.modifiers(),
        )
        if event.type() == QEvent.Type.MouseMove:
            region.mouseMoveEvent(forwarded)
        elif event.type() == QEvent.Type.MouseButtonRelease:
            region.mouseReleaseEvent(forwarded)

    def _regions_at(self, view_pos) -> list[RegionItem]:  # noqa: ANN001
        """RegionItems under the point, top-most first (Qt items() order)."""
        return [item for item in self.items(view_pos) if isinstance(item, RegionItem)]

    def _region_at(self, view_pos, *, cycle: bool = False) -> RegionItem | None:  # noqa: ANN001
        # Prefer RegionItem even when chrome (playhead, labels) overlaps the click.
        # Selected regions rise above same-lane neighbors, so a normal click keeps
        # the current selection for drag/trim. Alt+click cycles the stack.
        stack = self._regions_at(view_pos)
        if not stack:
            return None
        if not cycle or len(stack) == 1:
            return stack[0]
        selected = self.selected_operation_id()
        if selected is None:
            return stack[0]
        ids = [region.operation_id for region in stack]
        if selected not in ids:
            return stack[0]
        return stack[(ids.index(selected) + 1) % len(stack)]

    def _suggestion_at(self, view_pos: QPoint) -> SuggestionPlaceholderItem | None:
        for item in self.items(view_pos):
            if isinstance(item, SuggestionPlaceholderItem):
                return item
        return None

    def _flag_at(self, view_pos) -> FlagMarkerItem | None:  # noqa: ANN001
        scene_pos = self.mapToScene(view_pos)
        for item in self._flags.values():
            if item.contains(item.mapFromScene(scene_pos)):
                return item
        return None

    def _move_active_flag_from_view_x(self, view_x: float) -> None:
        if not self._flag_did_drag:
            if abs(view_x - self._flag_press_view_x) < _FLAG_DRAG_THRESHOLD_PX:
                return
            self._flag_did_drag = True
            self.playhead_scrub_started.emit()
        self._move_active_flag(self._view_x_to_time(view_x))

    def _move_active_flag(self, seconds: float) -> None:
        flag = self._active_flag
        if flag is None:
            return
        seconds = max(0.0, min(self._duration, seconds))
        if abs(seconds - flag.seconds()) < 1e-6:
            return
        flag.set_seconds(seconds)
        self.set_playhead(seconds, notify=True)
        self.flag_moved.emit(flag.flag_id, seconds, False)
        self._sync_navigator()

    def _nudge_step_s(self, *, fine: bool) -> float:
        if fine:
            return 0.01
        # At least one 24fps frame, and at least one screen pixel at the current zoom.
        pixel_s = 1.0 / max(1.0, self._pps)
        return max(pixel_s, 1.0 / 24.0)

    def wheelEvent(self, event) -> None:  # noqa: ANN001, N802
        if not self._edits_enabled:
            event.accept()
            return
        if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            delta = event.angleDelta().y()
            factor = 1.15 if delta > 0 else 1 / 1.15
            anchor_x = float(event.position().x())
            self.set_pixels_per_second(self._pps * factor, anchor_view_x=anchor_x)
            event.accept()
            return
        delta = event.angleDelta().y()
        if delta != 0:
            bar = self.horizontalScrollBar()
            max(1, bar.singleStep())
            new_val = max(bar.minimum(), min(bar.maximum(), bar.value() - delta // 8))
            if new_val != bar.value():
                bar.setValue(new_val)
            event.accept()
            return
        super().wheelEvent(event)

    def mousePressEvent(self, event) -> None:  # noqa: ANN001, N802
        if not self._edits_enabled:
            event.accept()
            return
        self.setFocus()
        scene_pos = self.mapToScene(event.position().toPoint())
        t = self._scene_x_to_time(scene_pos.x())
        flag = self._flag_at(event.position().toPoint())
        if flag is not None and event.button() in (
            Qt.MouseButton.LeftButton,
            Qt.MouseButton.RightButton,
        ):
            self.select_flag(flag.flag_id)
            self.flag_selected.emit(flag.flag_id)
            if event.button() == Qt.MouseButton.RightButton:
                self.flag_context_menu_requested.emit(
                    flag.flag_id,
                    event.globalPosition().toPoint(),
                )
            else:
                self.go_to_time(flag.seconds(), scroll_into_view=False)
                self._active_flag = flag
                self._flag_did_drag = False
                self._flag_press_view_x = float(event.position().x())
            event.accept()
            return

        if event.button() == Qt.MouseButton.RightButton:
            # Ruler and the track gutter never create regions.
            if is_ruler_y(scene_pos.y()) or is_track_gutter_y(scene_pos.y(), self._lane_height):
                event.accept()
                return
            if self._region_at(event.position().toPoint()) is not None:
                event.accept()
                return
            suggestion = self._suggestion_at(event.position().toPoint())
            if suggestion is not None:
                self.select_suggestion(suggestion.suggestion_id)
                event.accept()
                return
            self._clear_empty_drag()
            self._clear_right_drag()
            self._right_drag_start_s = t
            self._right_drag_current_s = t
            self._right_drag_op_type = self._draw_type_for_lane(
                lane_at_y(scene_pos.y(), self._lane_height)
            )
            self._refresh_draw_preview()
            event.accept()
            return

        if event.button() != Qt.MouseButton.LeftButton:
            super().mousePressEvent(event)
            return

        # Ruler: seek/scrub only — never create or select.
        if is_ruler_y(scene_pos.y()):
            self.clear_flag_selection()
            self.clear_suggestion_selection()
            if self.clear_operation_selection():
                self.operation_deselected.emit()
            self.set_playhead(t, notify=False)
            self._empty_drag_start_s = t
            self._empty_drag_current_s = t
            self._empty_drag_kind = "scrub"
            self._empty_drag_scrub = True
            self.playhead_scrub_started.emit()
            event.accept()
            return

        region = self._region_at(
            event.position().toPoint(),
            cycle=bool(event.modifiers() & Qt.KeyboardModifier.AltModifier),
        )

        if region is not None:
            self.clear_flag_selection()
            self.clear_suggestion_selection()
            self._clear_right_drag()
            self._clear_empty_drag()
            was_selected = region.isSelected()
            region._chrome_edits_on_press = was_selected  # noqa: SLF001
            local = region.mapFromScene(scene_pos)
            press = QMouseEvent(
                QEvent.Type.MouseButtonPress,
                local,
                scene_pos,
                event.globalPosition(),
                event.button(),
                event.buttons(),
                event.modifiers(),
            )
            region.mousePressEvent(press)
            for item in self._regions.values():
                item.setSelected(item is region)
            self._active_region = region
            if self._emit_trim_preview(region) is not None:
                self.playhead_scrub_started.emit()
            self.operation_selected.emit(region.operation_id)
            event.accept()
            return

        suggestion = self._suggestion_at(event.position().toPoint())
        if suggestion is not None:
            self.select_suggestion(suggestion.suggestion_id)
            event.accept()
            return

        # Gutter: seek/scrub only — never create a region in the gap.
        if is_track_gutter_y(scene_pos.y(), self._lane_height):
            self.clear_flag_selection()
            self.clear_suggestion_selection()
            if self.clear_operation_selection():
                self.operation_deselected.emit()
            self.set_playhead(t, notify=False)
            self._empty_drag_start_s = t
            self._empty_drag_current_s = t
            self._empty_drag_kind = "scrub"
            self._empty_drag_scrub = True
            self.playhead_scrub_started.emit()
            event.accept()
            return

        # Empty lane / background: click seeks, drag draws, Alt-drag scrubs.
        self.clear_flag_selection()
        self.clear_suggestion_selection()
        if self.clear_operation_selection():
            self.operation_deselected.emit()
        force_scrub = bool(event.modifiers() & Qt.KeyboardModifier.AltModifier)
        self._empty_drag_start_s = t
        self._empty_drag_current_s = t
        self._empty_press_view_x = float(event.position().x())
        if force_scrub:
            self.set_playhead(t, notify=False)
            self._empty_drag_kind = "scrub"
            self._empty_drag_scrub = True
            self.playhead_scrub_started.emit()
        else:
            self._empty_drag_kind = "undecided"
            self._empty_drag_scrub = False
            self._empty_drag_op_type = self._draw_type_for_lane(
                lane_at_y(scene_pos.y(), self._lane_height)
            )

        event.accept()

    def mouseMoveEvent(self, event) -> None:  # noqa: ANN001, N802
        if not self._edits_enabled:
            event.accept()
            return
        dragging = (
            bool(event.buttons() & (Qt.MouseButton.LeftButton | Qt.MouseButton.RightButton))
            and self._drag_is_active()
        )
        if dragging:
            self._drag_autoscroll_view_pos = QPointF(event.position())
            self._tick_drag_autoscroll()

        if self._active_flag is not None and event.buttons() & Qt.MouseButton.LeftButton:
            self._move_active_flag_from_view_x(float(event.position().x()))
            event.accept()
            return
        if self._active_region is not None and event.buttons() & Qt.MouseButton.LeftButton:
            self._forward_mouse_to_region(self._active_region, event)
            self._emit_trim_preview(self._active_region)
            event.accept()
            return
        if self._right_drag_start_s is not None and event.buttons() & Qt.MouseButton.RightButton:
            self._right_drag_current_s = self._view_x_to_time(float(event.position().x()))
            self._refresh_draw_preview()
            event.accept()
            return
        if self._empty_drag_start_s is not None and event.buttons() & Qt.MouseButton.LeftButton:
            t = self._view_x_to_time(float(event.position().x()))
            self._empty_drag_current_s = t
            if self._empty_drag_kind == "undecided" and (
                abs(float(event.position().x()) - self._empty_press_view_x)
                >= _EMPTY_DRAW_THRESHOLD_PX
            ):
                self._empty_drag_kind = "create"
            if self._empty_drag_kind == "scrub":
                self.set_playhead(t, notify=True)
            elif self._empty_drag_kind == "create":
                self._refresh_draw_preview()
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:  # noqa: ANN001, N802
        if not self._edits_enabled:
            event.accept()
            return
        if event.button() == Qt.MouseButton.RightButton and self._right_drag_start_s is not None:
            scene_pos = self.mapToScene(event.position().toPoint())
            end_s = self._scene_x_to_time(scene_pos.x())
            start_s = self._right_drag_start_s
            op_type = self._right_drag_op_type or self._draw_operation_type
            self._clear_right_drag()
            self._emit_region_created(start_s, end_s, op_type)
            event.accept()
            return

        if event.button() != Qt.MouseButton.LeftButton:
            super().mouseReleaseEvent(event)
            return

        if self._active_flag is not None:
            flag = self._active_flag
            did_drag = self._flag_did_drag
            self._active_flag = None
            self._flag_did_drag = False
            self._stop_drag_autoscroll_timer()
            if did_drag:
                self.flag_moved.emit(flag.flag_id, flag.seconds(), True)
            self.playhead_scrub_finished.emit(self._playhead_s)
            event.accept()
            return

        if self._active_region is not None:
            # Capture trim edge before mouseRelease clears the region's drag mode.
            edge = self._active_region.active_trim_edge_seconds()
            self._forward_mouse_to_region(self._active_region, event)
            self._active_region = None
            self._stop_drag_autoscroll_timer()
            if edge is not None:
                self.trim_preview_finished.emit(float(edge))
            event.accept()
            return

        if self._empty_drag_start_s is not None:
            scene_pos = self.mapToScene(event.position().toPoint())
            end_s = self._scene_x_to_time(scene_pos.x())
            start_s = self._empty_drag_start_s
            kind = self._empty_drag_kind
            op_type = self._empty_drag_op_type
            self._clear_empty_drag()

            if kind == "create" and op_type is not None:
                created = self._emit_region_created(start_s, end_s, op_type)
                if not created:
                    self.set_playhead(end_s, notify=False)
                    self.playhead_scrub_finished.emit(end_s)
            else:
                self.set_playhead(end_s, notify=False)
                self.playhead_scrub_finished.emit(end_s)
            event.accept()
            return

        super().mouseReleaseEvent(event)

    def keyPressEvent(self, event: QKeyEvent) -> None:  # noqa: N802
        if not self._edits_enabled:
            event.accept()
            return
        key = event.key()
        mods = event.modifiers()

        if key == Qt.Key.Key_D and not (mods & Qt.KeyboardModifier.ControlModifier):
            op_id = self.selected_operation_id()
            if op_id is not None:
                self.operation_toggle_enabled_requested.emit(op_id)
            event.accept()
            return

        if key in (Qt.Key.Key_Left, Qt.Key.Key_Right):
            sign = -1 if key == Qt.Key.Key_Left else 1
            if self.nudge_selected_region(
                sign,
                fine=bool(mods & Qt.KeyboardModifier.ShiftModifier),
                trim_in=bool(mods & Qt.KeyboardModifier.AltModifier),
                trim_out=bool(mods & Qt.KeyboardModifier.ControlModifier),
            ):
                event.accept()
                return
            super().keyPressEvent(event)
            return

        op_id = self.selected_operation_id()
        flag_id = self.selected_flag_id()
        suggestion_id = self.selected_suggestion_id()
        bare_mods = mods & ~Qt.KeyboardModifier.KeypadModifier
        if (
            key in (Qt.Key.Key_Return, Qt.Key.Key_Enter)
            and bare_mods == Qt.KeyboardModifier.NoModifier
            and suggestion_id is not None
        ):
            self.suggestion_accept_requested.emit(suggestion_id)
            event.accept()
            return
        if key in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace):
            if flag_id is not None:
                self.flag_delete_requested.emit(flag_id)
                event.accept()
                return
            if suggestion_id is not None:
                self.suggestion_reject_requested.emit(suggestion_id)
                event.accept()
                return
            if op_id is not None and op_id in self._regions:
                self.operation_delete_requested.emit(op_id)
                event.accept()
                return
            super().keyPressEvent(event)
            return
        if op_id is None or op_id not in self._regions:
            super().keyPressEvent(event)
            return
        super().keyPressEvent(event)
