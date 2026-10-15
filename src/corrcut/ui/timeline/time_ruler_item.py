"""Viewport-virtualized time ruler (ticks + compact labels)."""

from __future__ import annotations

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import QGraphicsItem, QStyleOptionGraphicsItem, QWidget

from corrcut.ui.timeline.time_ruler import (
    format_ruler_label,
    iter_ticks,
    visible_scene_cuts,
)
from corrcut.ui.timeline.timeline_lanes import RULER_HEIGHT


class TimeRulerItem(QGraphicsItem):
    """Paints major/minor ticks for the visible scroll window."""

    def __init__(self, height: float = RULER_HEIGHT) -> None:
        super().__init__()
        self._height = height
        self._duration = 1.0
        self._pps = 100.0
        self._scroll_x = 0.0
        self._viewport_width = 800.0
        self._scene_cut_times: tuple[float, ...] = ()
        self.setZValue(2)
        self.setAcceptedMouseButtons(Qt.MouseButton.NoButton)

    def boundingRect(self) -> QRectF:  # noqa: N802
        return QRectF(0, 0, self._viewport_width, self._height)

    def set_scene_cuts(self, times: tuple[float, ...] | list[float]) -> None:
        """Replace cut marks. ``times`` must be ascending; paint bisects them."""
        self._scene_cut_times = tuple(float(t) for t in times)

    def set_view_state(
        self,
        scroll_x: float,
        viewport_width: float,
        pps: float,
        duration: float,
        *,
        repaint: bool = True,
    ) -> None:
        viewport_width = max(1.0, viewport_width)
        pps = max(1.0, pps)
        duration = max(0.001, duration)
        changed = (
            self._scroll_x != scroll_x
            or self._viewport_width != viewport_width
            or self._pps != pps
            or self._duration != duration
        )
        if self._viewport_width != viewport_width:
            self.prepareGeometryChange()
        self._scroll_x = scroll_x
        self._viewport_width = viewport_width
        self._pps = pps
        self._duration = duration
        if changed and repaint:
            self.update()

    def paint(
        self,
        painter: QPainter,
        option: QStyleOptionGraphicsItem,
        widget: QWidget | None = None,
    ) -> None:
        del option, widget
        rect = self.boundingRect()
        painter.fillRect(rect, QColor(10, 10, 12))
        painter.setPen(QPen(QColor(36, 36, 40), 1))
        painter.drawLine(0, int(self._height) - 1, int(self._viewport_width), int(self._height) - 1)

        ticks = iter_ticks(
            scroll_x=self._scroll_x,
            viewport_width=self._viewport_width,
            pps=self._pps,
            duration=self._duration,
        )
        font = QFont("Segoe UI")
        font.setPixelSize(9)
        painter.setFont(font)
        metrics = painter.fontMetrics()

        major_pen = QPen(QColor(150, 150, 158, 160), 1)
        minor_pen = QPen(QColor(70, 70, 76, 150), 1)
        label_color = QColor(188, 188, 194)

        last_label_x = -1e9
        min_label_gap = 88.0

        for t, is_major in ticks:
            x = t * self._pps - self._scroll_x
            if x < -48 or x > self._viewport_width + 48:
                continue
            if is_major:
                painter.setPen(major_pen)
                painter.drawLine(int(x), int(self._height) - 5, int(x), int(self._height) - 1)
                if x - last_label_x >= min_label_gap:
                    label = format_ruler_label(t)
                    width = metrics.horizontalAdvance(label)
                    painter.setPen(label_color)
                    painter.drawText(
                        QRectF(x - width / 2.0, 0, width, self._height - 3),
                        int(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter),
                        label,
                    )
                    last_label_x = x
            else:
                painter.setPen(minor_pen)
                painter.drawLine(int(x), int(self._height) - 3, int(x), int(self._height) - 1)

        visible_cuts = visible_scene_cuts(
            self._scene_cut_times,
            scroll_x=self._scroll_x,
            viewport_width=self._viewport_width,
            pps=self._pps,
        )
        if visible_cuts:
            cut_pen = QPen(QColor(120, 170, 220, 110), 1)
            cut_pen.setCosmetic(True)
            painter.setPen(cut_pen)
            mark_bottom = int(self._height) - 1
            for t in visible_cuts:
                x = int(t * self._pps - self._scroll_x)
                painter.drawLine(x, 1, x, mark_bottom)
