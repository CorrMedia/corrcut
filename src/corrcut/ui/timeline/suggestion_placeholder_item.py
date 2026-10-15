"""Pending AI suggestion on the timeline: yellow strip with an edit label."""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QBrush, QColor, QFont, QFontMetrics, QPainter, QPen
from PySide6.QtWidgets import (
    QGraphicsItem,
    QGraphicsRectItem,
    QStyleOptionGraphicsItem,
    QWidget,
)

from corrcut.ai.normalize import strip_display_punctuation
from corrcut.domain.ai_suggestion import AiSuggestion
from corrcut.domain.corr_categories import censored_profanity_text
from corrcut.ui.timeline.region_geometry import MIN_REGION_DURATION_S
from corrcut.ui.timeline.timeline_lanes import TimelineLane, lane_rect

_HEADER_HEIGHT = 16.0


def suggestion_edit_label(suggestion: AiSuggestion) -> str:
    """Title for the mute accepting this suggestion would create."""
    word = suggestion.matched_word.strip() or suggestion.matched_term.strip()
    if word:
        word = strip_display_punctuation(word) or word
        shown = censored_profanity_text(
            word,
            matched_term=suggestion.matched_term,
            category=suggestion.category,
        )
        return f"Mute · {shown}"
    category = suggestion.category.strip()
    if category:
        return f"Mute · {category}"
    return "Mute"


class SuggestionPlaceholderItem(QGraphicsRectItem):
    """Dashed overlay for a pending suggestion. Click to select; Enter accepts, Delete rejects."""

    MIN_WIDTH = MIN_REGION_DURATION_S
    HEADER_HEIGHT = _HEADER_HEIGHT

    def __init__(
        self,
        suggestion_id: str,
        start_s: float,
        end_s: float,
        pixels_per_second: float,
        track_height: float,
        scroll_x: float,
        duration_s: float,
        label: str = "Mute",
    ) -> None:
        super().__init__()
        self.suggestion_id = suggestion_id
        self._label = label
        self._pps = pixels_per_second
        self._track_height = track_height
        self._scroll_x = scroll_x
        self._duration_s = max(0.001, duration_s)
        self._start_s = start_s
        self._end_s = end_s
        self.setAcceptedMouseButtons(Qt.MouseButton.LeftButton)
        self.setFlag(QGraphicsRectItem.GraphicsItemFlag.ItemIsSelectable, True)
        self.setZValue(0)
        self._apply_style()
        self._update_tool_tip()
        self._update_geometry()

    def label(self) -> str:
        return self._label

    def set_label(self, label: str) -> None:
        if label == self._label:
            return
        self._label = label
        self._update_tool_tip()
        self.update()

    def set_duration_limit(self, duration_s: float) -> None:
        duration_s = max(0.001, duration_s)
        if duration_s == self._duration_s:
            return
        self._duration_s = duration_s

    def set_scroll_x(self, scroll_x: float) -> None:
        if scroll_x == self._scroll_x:
            return
        self._scroll_x = scroll_x
        self._update_geometry()

    def set_lane_height(self, lane_height: float) -> None:
        if lane_height == self._track_height:
            return
        self._track_height = lane_height
        self._update_geometry()

    def set_pixels_per_second(self, pps: float) -> None:
        if pps == self._pps:
            return
        self._pps = pps
        self._update_geometry()

    def set_time_range(self, start_s: float, end_s: float) -> None:
        self._start_s = start_s
        self._end_s = end_s
        self._update_geometry()

    def itemChange(self, change: QGraphicsItem.GraphicsItemChange, value: Any) -> Any:
        if change == QGraphicsItem.GraphicsItemChange.ItemSelectedHasChanged:
            self.setZValue(0.5 if value else 0.0)
            self._apply_style()
            self.update()
        return super().itemChange(change, value)

    def _apply_style(self) -> None:
        selected = self.isSelected()
        self.setBrush(QBrush(QColor(255, 200, 60, 110 if selected else 55)))
        outline = QColor(255, 248, 220, 255) if selected else QColor(255, 220, 120, 220)
        pen = QPen(outline, 1.6 if selected else 1)
        if not selected:
            pen.setStyle(Qt.PenStyle.DashLine)
        self.setPen(pen)

    def _update_tool_tip(self) -> None:
        self.setToolTip(f"{self._label}\nEnter accepts · Delete rejects")

    def _update_geometry(self) -> None:
        new_rect = lane_rect(
            TimelineLane.AUDIO,
            self._start_s,
            self._end_s,
            self._pps,
            self._scroll_x,
            lane_height=self._track_height,
        )
        if self.rect() == new_rect:
            return
        self.setRect(new_rect)

    def _header_height(self, rect_height: float) -> float:
        return min(self.HEADER_HEIGHT, rect_height, max(10.0, rect_height * 0.28))

    def paint(
        self,
        painter: QPainter,
        option: QStyleOptionGraphicsItem,
        widget: QWidget | None = None,
    ) -> None:
        del option, widget
        rect = self.rect()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, False)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(self.brush())
        painter.drawRect(rect)

        header_h = self._header_height(rect.height())
        body_top = rect.top() + header_h
        if rect.width() >= 6 and rect.bottom() - body_top > 4:
            painter.setPen(QPen(QColor(255, 255, 255, 90), 1))
            step = 8.0
            x = rect.left() + step
            while x < rect.right():
                painter.drawLine(int(x), int(body_top), int(x - step), int(rect.bottom()))
                x += step

        header = rect.adjusted(0, 0, 0, -(rect.height() - header_h))
        header_fill = QColor(255, 214, 72, 255 if self.isSelected() else 230)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(header_fill)
        painter.drawRect(header)
        self._paint_label(painter, header)

        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(self.pen())
        painter.drawRect(rect)

    def _paint_label(self, painter: QPainter, header: QRectF) -> None:
        if header.width() < 22:
            return
        font = QFont(painter.font())
        font.setPixelSize(10)
        font.setBold(True)
        painter.setFont(font)
        text_rect = header.adjusted(6, 0, -4, 0)
        metrics = QFontMetrics(font)
        label = metrics.elidedText(
            self._label,
            Qt.TextElideMode.ElideRight,
            max(1, int(text_rect.width())),
        )
        painter.setPen(QColor(48, 32, 8, 245))
        painter.drawText(
            text_rect,
            int(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft),
            label,
        )
