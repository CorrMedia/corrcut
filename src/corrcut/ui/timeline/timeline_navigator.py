"""Full-clip overview bar so the visible window stays usable when zoomed in."""

from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QMouseEvent, QPainter, QPen, QWheelEvent
from PySide6.QtWidgets import QWidget

NAVIGATOR_HEIGHT = 16
MIN_HANDLE_WIDTH = 36.0

_BED = QColor(22, 22, 24)
_TRACK = QColor(40, 42, 48)
_HANDLE = QColor(92, 100, 118)
_HANDLE_EDGE = QColor(168, 176, 194)
_PLAYHEAD = QColor(255, 72, 72)


def navigator_max_scroll(duration_s: float, pps: float, viewport_width: float) -> float:
    content = max(1.0, duration_s * pps)
    return max(0.0, content - max(1.0, viewport_width))


def navigator_handle_size(
    *,
    bar_width: float,
    duration_s: float,
    viewport_width: float,
    pps: float,
    min_handle: float = MIN_HANDLE_WIDTH,
) -> float:
    """Handle width in bar pixels. Floor so a 2-hour clip at max zoom stays grabable."""
    width = max(1.0, bar_width)
    content = max(1.0, duration_s * pps)
    visible = min(content, max(1.0, viewport_width))
    if navigator_max_scroll(duration_s, pps, viewport_width) <= 0:
        return width
    return min(width, max(min_handle, width * (visible / content)))


def navigator_handle_rect(
    *,
    bar_width: float,
    bar_height: float,
    duration_s: float,
    scroll_x: float,
    viewport_width: float,
    pps: float,
    min_handle: float = MIN_HANDLE_WIDTH,
) -> QRectF:
    width = max(1.0, bar_width)
    height = max(1.0, bar_height)
    handle_w = navigator_handle_size(
        bar_width=width,
        duration_s=duration_s,
        viewport_width=viewport_width,
        pps=pps,
        min_handle=min_handle,
    )
    max_scroll = navigator_max_scroll(duration_s, pps, viewport_width)
    travel = width - handle_w
    if travel <= 0 or max_scroll <= 0:
        left = 0.0
    else:
        left = (max(0.0, min(max_scroll, scroll_x)) / max_scroll) * travel
    inset = 1.0
    return QRectF(left, inset, handle_w, max(1.0, height - 2.0 * inset))


def scroll_x_for_navigator_x(
    *,
    x: float,
    bar_width: float,
    duration_s: float,
    viewport_width: float,
    pps: float,
    min_handle: float = MIN_HANDLE_WIDTH,
) -> float:
    """scroll_x that centers the handle on x (clamped to the bar)."""
    width = max(1.0, bar_width)
    handle_w = navigator_handle_size(
        bar_width=width,
        duration_s=duration_s,
        viewport_width=viewport_width,
        pps=pps,
        min_handle=min_handle,
    )
    max_scroll = navigator_max_scroll(duration_s, pps, viewport_width)
    travel = width - handle_w
    if travel <= 0 or max_scroll <= 0:
        return 0.0
    left = max(0.0, min(travel, x - handle_w / 2.0))
    return (left / travel) * max_scroll


def navigator_pan_delta(
    mouse_dx: float,
    *,
    viewport_width: float,
    handle_width: float,
    bar_width: float = 0.0,
    max_scroll: float = 0.0,
    direct: bool = False,
) -> float:
    """Timeline pixels to pan for a handle drag.

    Uses the visible window vs handle size, not the full clip. When the handle
    is min-sized at high zoom, 1px of mouse no longer jumps seconds of media.
    At a natural (non-floored) handle this matches full-clip mapping.

    ``direct`` is a normal scrollbar: the thumb tracks the mouse 1:1, so the
    handle's travel across the bar covers the whole scroll range.
    """
    if direct:
        travel = max(0.0, bar_width - handle_width)
        if travel <= 0 or max_scroll <= 0:
            return 0.0
        return mouse_dx * (max_scroll / travel)
    if handle_width <= 0:
        return 0.0
    return mouse_dx * (max(1.0, viewport_width) / handle_width)


def navigator_scroll_after_pan(
    start_scroll: float,
    mouse_dx: float,
    *,
    duration_s: float,
    pps: float,
    viewport_width: float,
    handle_width: float,
    bar_width: float = 0.0,
    direct: bool = False,
) -> float:
    max_scroll = navigator_max_scroll(duration_s, pps, viewport_width)
    return max(
        0.0,
        min(
            max_scroll,
            start_scroll
            + navigator_pan_delta(
                mouse_dx,
                viewport_width=viewport_width,
                handle_width=handle_width,
                bar_width=bar_width,
                max_scroll=max_scroll,
                direct=direct,
            ),
        ),
    )


def _same_flags(
    current: list[tuple[float, QColor]],
    incoming: list[tuple[float, QColor]],
) -> bool:
    if len(current) != len(incoming):
        return False
    return all(
        old_s == new_s and old_color.rgba() == new_color.rgba()
        for (old_s, old_color), (new_s, new_color) in zip(current, incoming, strict=True)
    )


def playhead_navigator_x(playhead_s: float, duration_s: float, bar_width: float) -> float:
    width = max(1.0, bar_width)
    if duration_s <= 0:
        return 0.0
    return max(0.0, min(width, (playhead_s / duration_s) * width))


class TimelineNavigator(QWidget):
    """Click/drag overview of the whole clip; the window handle never shrinks away."""

    scroll_requested = Signal(float)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._duration = 1.0
        self._pps = 80.0
        self._scroll_x = 0.0
        self._viewport_width = 800.0
        self._playhead_s = 0.0
        self._flags: list[tuple[float, QColor]] = []
        self._drag_origin_x: float | None = None
        self._drag_origin_scroll: float | None = None
        self._drag_last_x: float | None = None
        self._drag_direct = False
        self._interactive = True
        self.setFixedHeight(NAVIGATOR_HEIGHT)
        self.setToolTip("Click to jump · drag to pan · Shift+drag for a normal scrollbar")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)

    def set_view_state(
        self,
        *,
        scroll_x: float,
        viewport_width: float,
        pps: float,
        duration: float,
        playhead_s: float,
    ) -> None:
        changed = (
            self._scroll_x != scroll_x
            or self._viewport_width != viewport_width
            or self._pps != pps
            or self._duration != duration
            or self._playhead_s != playhead_s
        )
        self._scroll_x = scroll_x
        self._viewport_width = max(1.0, viewport_width)
        self._pps = max(1.0, pps)
        self._duration = max(0.001, duration)
        self._playhead_s = playhead_s
        if changed:
            self.update()

    def set_flags(self, flags: list[tuple[float, QColor]]) -> None:
        if _same_flags(self._flags, flags):
            return
        self._flags = list(flags)
        self.update()

    def handle_rect(self) -> QRectF:
        return navigator_handle_rect(
            bar_width=float(self.width()),
            bar_height=float(self.height()),
            duration_s=self._duration,
            scroll_x=self._scroll_x,
            viewport_width=self._viewport_width,
            pps=self._pps,
        )

    def paintEvent(self, event) -> None:  # noqa: ANN001, N802
        del event
        painter = QPainter(self)
        painter.fillRect(self.rect(), _BED)
        track = self.rect().adjusted(0, 3, 0, -3)
        painter.fillRect(track, _TRACK)
        handle = self.handle_rect()
        painter.setPen(QPen(_HANDLE_EDGE, 1))
        painter.setBrush(_HANDLE)
        painter.drawRect(handle)
        for seconds, color in self._flags:
            fx = playhead_navigator_x(seconds, self._duration, float(self.width()))
            tick = QColor(color)
            tick.setAlpha(220)
            painter.setPen(QPen(tick, 2))
            painter.drawLine(QPointF(fx, 2.0), QPointF(fx, float(self.height()) - 2.0))
        x = playhead_navigator_x(self._playhead_s, self._duration, float(self.width()))
        painter.setPen(QPen(_PLAYHEAD, 1))
        painter.drawLine(QPointF(x, 1.0), QPointF(x, float(self.height()) - 1.0))

    def set_interactive(self, enabled: bool) -> None:
        self._interactive = bool(enabled)
        self.setCursor(
            Qt.CursorShape.PointingHandCursor if self._interactive else Qt.CursorShape.ArrowCursor
        )

    def mousePressEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if not self._interactive:
            event.accept()
            return
        if event.button() != Qt.MouseButton.LeftButton:
            return
        x = float(event.position().x())
        handle = self.handle_rect()
        start_scroll = self._scroll_x
        if not handle.contains(event.position()):
            start_scroll = scroll_x_for_navigator_x(
                x=x,
                bar_width=float(self.width()),
                duration_s=self._duration,
                viewport_width=self._viewport_width,
                pps=self._pps,
            )
            self._scroll_x = start_scroll
            self.scroll_requested.emit(start_scroll)
        self._drag_origin_x = x
        self._drag_origin_scroll = start_scroll
        self._drag_last_x = x
        self._drag_direct = self._shift_direct(event)
        self.setCursor(Qt.CursorShape.ClosedHandCursor)
        event.accept()

    def mouseMoveEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if not self._interactive:
            event.accept()
            return
        if self._drag_origin_x is None or not (event.buttons() & Qt.MouseButton.LeftButton):
            return
        x = float(event.position().x())
        direct = self._shift_direct(event)
        if direct != self._drag_direct:
            # Keep the current window, then apply this move with the new mapping.
            self._drag_origin_x = self._drag_last_x if self._drag_last_x is not None else x
            self._drag_origin_scroll = self._scroll_x
            self._drag_direct = direct
        self._drag_last_x = x
        dx = x - (self._drag_origin_x or x)
        self.scroll_requested.emit(
            navigator_scroll_after_pan(
                self._drag_origin_scroll or 0.0,
                dx,
                duration_s=self._duration,
                pps=self._pps,
                viewport_width=self._viewport_width,
                handle_width=self.handle_rect().width(),
                bar_width=float(self.width()),
                direct=direct,
            )
        )
        event.accept()

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_origin_x = None
            self._drag_origin_scroll = None
            self._drag_last_x = None
            self._drag_direct = False
            self.setCursor(Qt.CursorShape.PointingHandCursor)
            event.accept()

    def _shift_direct(self, event: QMouseEvent) -> bool:
        return bool(event.modifiers() & Qt.KeyboardModifier.ShiftModifier)

    def wheelEvent(self, event: QWheelEvent) -> None:  # noqa: N802
        if not self._interactive:
            event.accept()
            return
        parent = self.parent()
        if parent is not None and hasattr(parent, "wheelEvent"):
            parent.wheelEvent(event)
            return
        super().wheelEvent(event)
