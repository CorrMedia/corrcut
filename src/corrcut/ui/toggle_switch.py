"""Compact on/off switch for inspector boolean fields."""

from __future__ import annotations

from PySide6.QtCore import QRectF, QSize, Qt
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPalette
from PySide6.QtWidgets import QAbstractButton, QSizePolicy, QWidget

_TRACK_W = 40
_TRACK_H = 22
_PAD = 2
_OFF = QColor("#4a505c")
_KNOB = QColor("#f0f0f0")


class ToggleSwitch(QAbstractButton):
    """Checkable switch with the same isChecked/setChecked/toggled API as QCheckBox."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setCheckable(True)
        self.setChecked(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self.setFixedSize(_TRACK_W, _TRACK_H)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

    def sizeHint(self) -> QSize:  # noqa: N802
        return self.minimumSizeHint()

    def minimumSizeHint(self) -> QSize:  # noqa: N802
        return QSize(_TRACK_W, _TRACK_H)

    def paintEvent(self, event) -> None:  # type: ignore[no-untyped-def]  # noqa: N802
        del event
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        on = self.isChecked()
        # Same Highlight as selected tool-strip buttons and the channel map.
        track_color = self.palette().color(QPalette.ColorRole.Highlight) if on else _OFF
        track = QRectF(0.5, 0.5, _TRACK_W - 1.0, _TRACK_H - 1.0)
        path = QPainterPath()
        path.addRoundedRect(track, (_TRACK_H - 1.0) / 2.0, (_TRACK_H - 1.0) / 2.0)
        painter.fillPath(path, track_color)
        knob_d = _TRACK_H - 2 * _PAD
        knob_x = _TRACK_W - _PAD - knob_d if on else float(_PAD)
        knob = QRectF(knob_x, float(_PAD), float(knob_d), float(knob_d))
        painter.setBrush(_KNOB)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawEllipse(knob)
        painter.end()
