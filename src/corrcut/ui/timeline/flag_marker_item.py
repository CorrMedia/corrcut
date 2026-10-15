"""Point flag marker drawn in the timeline ruler."""

from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen, QPolygonF
from PySide6.QtWidgets import QGraphicsItem, QStyleOptionGraphicsItem, QWidget

from corrcut.ui.timeline.timecode import format_timecode

_FLAG_WIDTH = 12.0
_FLAG_HEIGHT = 16.0
_FLAG_POINT = 6.0
_FLAG_TOP = 1.5


def flag_draw_x(scene_x: float) -> float:
    """Same pixel-center snap as the playhead so the ribbon tip sits on the line."""
    return round(scene_x) + 0.5


def ribbon_polygon(
    center_x: float = 0.0,
    *,
    top: float = _FLAG_TOP,
    width: float = _FLAG_WIDTH,
    height: float = _FLAG_HEIGHT,
    point_height: float = _FLAG_POINT,
) -> QPolygonF:
    """Bookmark ribbon: pentagon body with a centered tip."""
    half = width / 2.0
    left = center_x - half
    right = center_x + half
    body_bottom = top + height - point_height
    tip_y = top + height
    return QPolygonF(
        [
            QPointF(left, top),
            QPointF(right, top),
            QPointF(right, body_bottom),
            QPointF(center_x, tip_y),
            QPointF(left, body_bottom),
        ]
    )


class FlagMarkerItem(QGraphicsItem):
    """Bookmark ribbon on the ruler; drag to move, left-click to seek, Delete to remove."""

    def __init__(
        self,
        flag_id: str,
        seconds: float,
        slot: int,
        color: QColor,
        label: str,
        pixels_per_second: float,
        scroll_x: float,
    ) -> None:
        super().__init__()
        self.flag_id = flag_id
        self._seconds = float(seconds)
        self._slot = int(slot)
        self._color = QColor(color)
        self._label = label
        self._pps = pixels_per_second
        self._scroll_x = scroll_x
        self.setZValue(12)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable, True)
        self.setAcceptedMouseButtons(Qt.MouseButton.LeftButton | Qt.MouseButton.RightButton)
        self._update_tool_tip()
        self._update_pos()

    def seconds(self) -> float:
        return self._seconds

    def slot(self) -> int:
        return self._slot

    def color(self) -> QColor:
        return QColor(self._color)

    def set_slot(self, slot: int) -> None:
        self._slot = int(slot)

    def set_appearance(self, color: QColor, label: str) -> None:
        self._color = QColor(color)
        self._label = label
        self._update_tool_tip()
        self.update()

    def set_seconds(self, seconds: float) -> None:
        self._seconds = float(seconds)
        self._update_tool_tip()
        self._update_pos()

    def set_scroll_x(self, scroll_x: float) -> None:
        if scroll_x == self._scroll_x:
            return
        self._scroll_x = scroll_x
        self._update_pos()

    def set_pixels_per_second(self, pps: float) -> None:
        if pps == self._pps:
            return
        self._pps = pps
        self._update_pos()

    def boundingRect(self) -> QRectF:  # noqa: N802
        half = _FLAG_WIDTH / 2.0
        return QRectF(
            -half - 1.5,
            0.0,
            _FLAG_WIDTH + 3.0,
            _FLAG_TOP + _FLAG_HEIGHT + 1.5,
        )

    def shape(self) -> QPainterPath:  # noqa: N802
        path = QPainterPath()
        path.addPolygon(ribbon_polygon(0.0))
        return path

    def paint(
        self,
        painter: QPainter,
        option: QStyleOptionGraphicsItem,
        widget: QWidget | None = None,
    ) -> None:
        del option, widget
        body = ribbon_polygon(0.0)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        fill = QColor(self._color)
        fill.setAlpha(255)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(fill)
        painter.drawPolygon(body)
        outline = QColor(255, 255, 255, 230) if self.isSelected() else QColor(18, 12, 14, 210)
        painter.setPen(QPen(outline, 1.2 if self.isSelected() else 0.9))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawPolygon(body)

    def _update_tool_tip(self) -> None:
        text = self._label.strip() or f"Flag {self._slot}"
        self.setToolTip(f"{text}  {format_timecode(self._seconds)}")

    def _update_pos(self) -> None:
        x = flag_draw_x(self._seconds * self._pps - self._scroll_x)
        self.setPos(x, 0.0)
