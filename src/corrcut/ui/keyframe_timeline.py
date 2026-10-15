"""Inspector-style keyframe track for the selected spatial edit."""

from __future__ import annotations

import math

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPen, QPolygonF
from PySide6.QtWidgets import QHBoxLayout, QLabel, QWidget

_TRACK_H = 46
_PAD = 8.0
_HIT = 10.0
_DRAG_PX = 5.0
_DIAMOND = 6.0
_TICK_STEPS = (0.1, 0.2, 0.5, 1.0, 2.0, 5.0, 10.0, 15.0, 30.0, 60.0, 120.0)


def nice_tick_step(span: float) -> float:
    raw = max(float(span), 1e-6) / 6.0
    for step in _TICK_STEPS:
        if step >= raw:
            return step
    return 120.0


def tick_seconds(start: float, end: float) -> list[float]:
    start = float(start)
    end = max(float(end), start + 1e-6)
    step = nice_tick_step(end - start)
    ticks = [start]
    grid = math.ceil((start + step * 0.2) / step) * step
    while grid < end - step * 0.2:
        ticks.append(float(grid))
        grid += step
    ticks.append(end)
    return ticks


def view_span(
    enable_start: float,
    enable_end: float,
    times: list[float] | None = None,
) -> tuple[float, float]:
    """Ruler window: enable span plus room for keys that sit outside it."""
    enable_start = float(enable_start)
    enable_end = max(float(enable_end), enable_start + 1e-6)
    span = enable_end - enable_start
    pad = max(0.5, min(3.0, span * 0.15))
    points = [enable_start, enable_end, *(float(value) for value in (times or []))]
    start = min(points) - pad
    end = max(points) + pad
    return max(0.0, start), max(end, start + 1e-6)


def format_track_time(seconds: float) -> str:
    seconds = max(0.0, float(seconds))
    if seconds >= 60.0:
        minutes = int(seconds // 60.0)
        rest = seconds - minutes * 60.0
        if abs(rest - round(rest)) < 1e-6:
            return f"{minutes}:{int(round(rest)):02d}"
        return f"{minutes}:{rest:04.1f}"
    if abs(seconds - round(seconds)) < 1e-6:
        return f"{int(round(seconds))}s"
    return f"{seconds:.1f}s"


class _Track(QWidget):
    seek_requested = Signal(float)
    seek_finished = Signal(float)
    index_selected = Signal(int)
    background_pressed = Signal()
    key_moved = Signal(int, float)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setMinimumHeight(_TRACK_H)
        self.setMouseTracking(True)
        self.setToolTip(
            "Click a diamond to select it · click empty to scrub · Delete follows last click"
        )
        self._start = 0.0
        self._end = 1.0
        self._enable_start = 0.0
        self._enable_end = 1.0
        self._fit_enabled = False
        self._times: list[float] = []
        self._active = 0
        self._selected = -1
        self._playhead = 0.0
        self._drag_index: int | None = None
        self._pending_index: int | None = None
        self._press_pos: QPointF | None = None
        self._press_seconds: float | None = None
        self._scrubbing = False

    def set_span(self, start: float, end: float) -> None:
        self._start = float(start)
        self._end = max(float(end), self._start + 1e-6)
        self._enable_start = self._start
        self._enable_end = self._end
        self._fit_enabled = False
        self.update()

    def set_enable_window(self, start: float, end: float) -> None:
        self._enable_start = float(start)
        self._enable_end = max(float(end), self._enable_start + 1e-6)
        self._fit_enabled = True
        self._fit_view()
        self.update()

    def set_keys(self, times: list[float], active: int, selected: int = -1) -> None:
        self._times = [float(value) for value in times]
        self._active = max(0, min(int(active), max(0, len(self._times) - 1)))
        if 0 <= int(selected) < len(self._times):
            self._selected = int(selected)
        else:
            self._selected = -1
        if self._fit_enabled:
            self._fit_view()
        self.update()

    def _fit_view(self) -> None:
        self._start, self._end = view_span(self._enable_start, self._enable_end, self._times)

    def _outside_enable(self, seconds: float) -> bool:
        return (
            float(seconds) < self._enable_start - 1e-3 or float(seconds) > self._enable_end + 1e-3
        )

    def set_active(self, index: int) -> None:
        if not self._times:
            self._active = 0
        else:
            self._active = max(0, min(int(index), len(self._times) - 1))
        self.update()

    def set_selected(self, index: int) -> None:
        if 0 <= int(index) < len(self._times):
            self._selected = int(index)
        else:
            self._selected = -1
        self.update()

    def set_playhead(self, seconds: float) -> None:
        self._playhead = float(seconds)
        self.update()

    def _track_rect(self) -> QRectF:
        return QRectF(_PAD, 16.0, max(1.0, self.width() - _PAD * 2.0), self.height() - 20.0)

    def _seconds_to_x(self, seconds: float) -> float:
        span = self._end - self._start
        frac = 0.0 if span <= 1e-9 else (float(seconds) - self._start) / span
        rect = self._track_rect()
        return rect.left() + max(0.0, min(1.0, frac)) * rect.width()

    def _x_to_seconds(self, x: float) -> float:
        rect = self._track_rect()
        frac = 0.0 if rect.width() <= 1 else (float(x) - rect.left()) / rect.width()
        frac = max(0.0, min(1.0, frac))
        return self._start + frac * (self._end - self._start)

    def _hit_index(self, pos: QPointF) -> int | None:
        best: int | None = None
        best_dist = _HIT
        mid = self._track_rect().center().y()
        for index, seconds in enumerate(self._times):
            dist = abs(self._seconds_to_x(seconds) - pos.x()) + abs(mid - pos.y()) * 0.25
            if dist < best_dist:
                best_dist = dist
                best = index
        return best

    def _snap_seconds(self, seconds: float) -> tuple[float, int | None]:
        x = self._seconds_to_x(seconds)
        best: int | None = None
        best_dist = _HIT
        for index, time in enumerate(self._times):
            dist = abs(self._seconds_to_x(time) - x)
            if dist < best_dist:
                best_dist = dist
                best = index
        if best is None:
            return seconds, None
        return self._times[best], best

    def _tick_font(self) -> QFont:
        font = QFont(self.font())
        font.setPixelSize(9)
        return font

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = self._track_rect()
        mid = rect.center().y()
        painter.setFont(self._tick_font())
        painter.setPen(QPen(QColor(255, 255, 255, 70), 1))
        for seconds in tick_seconds(self._start, self._end):
            x = self._seconds_to_x(seconds)
            painter.drawLine(QPointF(x, 2.0), QPointF(x, 11.0))
            label = format_track_time(seconds)
            painter.setPen(QColor(180, 180, 180, 210))
            painter.drawText(QPointF(x + 2.0, 11.0), label)
            painter.setPen(QPen(QColor(255, 255, 255, 70), 1))
        enable_left = self._seconds_to_x(self._enable_start)
        enable_right = self._seconds_to_x(self._enable_end)
        painter.fillRect(
            QRectF(enable_left, rect.top(), max(1.0, enable_right - enable_left), rect.height()),
            QColor(0, 180, 220, 32),
        )
        painter.setPen(QPen(QColor(0, 200, 230, 160), 1.4))
        painter.drawLine(QPointF(enable_left, rect.top()), QPointF(enable_left, rect.bottom()))
        painter.drawLine(QPointF(enable_right, rect.top()), QPointF(enable_right, rect.bottom()))
        painter.setPen(QPen(QColor(255, 255, 255, 40), 2))
        painter.drawLine(QPointF(rect.left(), mid), QPointF(rect.right(), mid))
        if self._times:
            first = self._seconds_to_x(self._times[0])
            last = self._seconds_to_x(self._times[-1])
            painter.setPen(QPen(QColor(0, 180, 220, 90), 2))
            painter.drawLine(QPointF(first, mid), QPointF(last, mid))
        play_x = self._seconds_to_x(self._playhead)
        painter.setPen(QPen(QColor(255, 210, 70, 230), 1.5))
        painter.drawLine(QPointF(play_x, 1.0), QPointF(play_x, self.height() - 2.0))
        for index, seconds in enumerate(self._times):
            cx = self._seconds_to_x(seconds)
            selected = index == self._selected
            outside = self._outside_enable(seconds)
            r = _DIAMOND + (1.5 if selected else 0.0)
            diamond = QPolygonF(
                [
                    QPointF(cx, mid - r),
                    QPointF(cx + r, mid),
                    QPointF(cx, mid + r),
                    QPointF(cx - r, mid),
                ]
            )
            if selected and outside:
                painter.setBrush(QColor(220, 50, 50, 255))
                painter.setPen(QPen(QColor(255, 230, 140, 255), 2.0))
            elif selected:
                painter.setBrush(QColor(0, 220, 255, 255))
                painter.setPen(QPen(QColor(255, 230, 140, 255), 2.0))
            elif outside:
                painter.setBrush(QColor(220, 50, 50, 200))
                painter.setPen(QPen(QColor(255, 170, 170, 255), 1.1))
            else:
                painter.setBrush(QColor(230, 230, 235, 240))
                painter.setPen(QPen(QColor(30, 34, 38, 200), 1.2))
            painter.drawPolygon(diamond)

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() != Qt.MouseButton.LeftButton:
            event.ignore()
            return
        pos = event.position() if hasattr(event, "position") else QPointF(event.pos())
        index = self._hit_index(pos)
        seconds = self._x_to_seconds(pos.x())
        self._press_pos = QPointF(pos)
        self._press_seconds = seconds
        if index is not None:
            self._pending_index = index
            self._drag_index = None
            self._scrubbing = False
            self._active = index
            self._selected = index
            self._playhead = self._times[index]
            self.index_selected.emit(index)
            self.update()
        else:
            self._pending_index = None
            self._drag_index = None
            self._scrubbing = True
            self._selected = -1
            self._playhead = seconds
            self.update()
            self.background_pressed.emit()
            self.seek_requested.emit(seconds)
        event.accept()

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        pos = event.position() if hasattr(event, "position") else QPointF(event.pos())
        if self._pending_index is not None and self._press_pos is not None:
            if (pos - self._press_pos).manhattanLength() < _DRAG_PX:
                return
            self._drag_index = self._pending_index
            self._pending_index = None
        if self._drag_index is None and not self._scrubbing:
            hit = self._hit_index(pos) is not None
            self.setCursor(Qt.CursorShape.SizeHorCursor if hit else Qt.CursorShape.ArrowCursor)
            return
        seconds = self._x_to_seconds(pos.x())
        self._playhead = seconds
        if self._drag_index is not None:
            self._times[self._drag_index] = seconds
            self._active = self._drag_index
            if self._fit_enabled:
                self._fit_view()
        self.update()
        self.seek_requested.emit(seconds)
        event.accept()

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        if self._drag_index is not None:
            self.key_moved.emit(self._drag_index, self._times[self._drag_index])
        elif self._scrubbing:
            snapped, index = self._snap_seconds(self._playhead)
            if index is not None:
                self._playhead = snapped
                self._active = index
                self.update()
            self.seek_finished.emit(self._playhead)
        self._drag_index = None
        self._pending_index = None
        self._press_pos = None
        self._press_seconds = None
        self._scrubbing = False
        event.accept()


class KeyframeTimeline(QWidget):
    """Resolve-style keyframe row for the selected cover/zoom box."""

    seek_requested = Signal(float)
    seek_finished = Signal(float)
    add_requested = Signal(float)
    remove_requested = Signal()
    index_selected = Signal(int)
    background_pressed = Signal()
    key_moved = Signal(int, float)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFixedHeight(_TRACK_H + 8)
        self.setStyleSheet("background: #1a1a1a;")
        row = QHBoxLayout(self)
        row.setContentsMargins(8, 4, 8, 4)
        row.setSpacing(6)
        label = QLabel("Box")
        font = QFont(label.font())
        font.setPixelSize(11)
        font.setWeight(QFont.Weight.DemiBold)
        label.setFont(font)
        label.setStyleSheet("color: #bbb;")
        label.setFixedWidth(36)
        self._track = _Track(self)
        self._time_label = QLabel("0s")
        time_font = QFont(self._time_label.font())
        time_font.setPixelSize(10)
        self._time_label.setFont(time_font)
        self._time_label.setStyleSheet("color: #e6c84a;")
        self._time_label.setFixedWidth(44)
        self._time_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        row.addWidget(label)
        row.addWidget(self._track, stretch=1)
        row.addWidget(self._time_label)
        self._track.seek_requested.connect(self.seek_requested.emit)
        self._track.seek_finished.connect(self.seek_finished.emit)
        self._track.index_selected.connect(self.index_selected.emit)
        self._track.background_pressed.connect(self.background_pressed.emit)
        self._track.key_moved.connect(self.key_moved.emit)
        self._key_count = 0
        self.setFocusPolicy(Qt.FocusPolicy.ClickFocus)
        self.hide()

    def set_span(self, start: float, end: float) -> None:
        self._track.set_span(start, end)

    def set_enable_window(self, start: float, end: float) -> None:
        self._track.set_enable_window(start, end)

    def set_keys(self, times: list[float], active: int = 0, selected: int = -1) -> None:
        self._key_count = len(times)
        self._track.set_keys(times, active, selected)
        self.setVisible(True)

    def set_active(self, index: int) -> None:
        self._track.set_active(index)

    def set_selected(self, index: int) -> None:
        self._track.set_selected(index)

    def set_playhead(self, seconds: float) -> None:
        self._track.set_playhead(seconds)
        self._time_label.setText(format_track_time(seconds))

    @property
    def key_times(self) -> list[float]:
        return list(self._track._times)

    @property
    def active_index(self) -> int:
        return self._track._active

    @property
    def selected_index(self) -> int:
        return self._track._selected

    @property
    def playhead_seconds(self) -> float:
        return self._track._playhead

    @property
    def view_range(self) -> tuple[float, float]:
        return self._track._start, self._track._end

    @property
    def enable_window(self) -> tuple[float, float]:
        return self._track._enable_start, self._track._enable_end

    @property
    def delete_enabled(self) -> bool:
        return self._key_count > 1

    def clear(self) -> None:
        self._key_count = 0
        self._track.set_keys([], 0, -1)
        self.hide()
