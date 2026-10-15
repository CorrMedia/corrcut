"""Icon-only transport controls styled like typical NLE toolbars."""

from __future__ import annotations

from enum import Enum

from PySide6.QtCore import QPointF, QRectF, QSize, Qt
from PySide6.QtGui import QBrush, QColor, QIcon, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import QToolButton

_TRANSPORT_ICON_SIZE = QSize(24, 24)
_TRANSPORT_BTN_SIZE = QSize(34, 34)
_TRANSPORT_PLAY_BTN_SIZE = QSize(38, 38)
_TRANSPORT_PLAY_ICON_SIZE = QSize(26, 26)

_CANVAS = 24.0
_ICON_COLOR = QColor("#e8e8e8")
_STROKE = 1.75

TRANSPORT_BUTTON_STYLE = """
QToolButton {
    background-color: transparent;
    border: none;
    border-radius: 4px;
}
QToolButton:hover {
    background-color: rgba(255, 255, 255, 0.10);
}
QToolButton:pressed {
    background-color: rgba(255, 255, 255, 0.16);
}
QToolButton:disabled {
    background-color: transparent;
}
"""

OVERLAY_TRANSPORT_BUTTON_STYLE = """
QToolButton {
    background-color: rgba(0, 0, 0, 0.55);
    border: none;
    border-radius: 4px;
}
QToolButton:hover {
    background-color: rgba(255, 255, 255, 0.14);
}
QToolButton:pressed {
    background-color: rgba(255, 255, 255, 0.22);
}
QToolButton:disabled {
    background-color: rgba(0, 0, 0, 0.35);
}
"""


class TransportIconKind(Enum):
    PLAY = "play"
    PAUSE = "pause"
    SEEK_START = "seek_start"
    SEEK_END = "seek_end"
    FRAME_BACK = "frame_back"
    FRAME_FWD = "frame_fwd"
    FULLSCREEN_ENTER = "fullscreen_enter"
    FULLSCREEN_EXIT = "fullscreen_exit"
    VOLUME = "volume"
    VOLUME_MUTED = "volume_muted"


def transport_icon(kind: TransportIconKind, size: int = 24) -> QIcon:
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    scale = size / _CANVAS
    painter.scale(scale, scale)
    _paint_icon(painter, kind)
    painter.end()
    return QIcon(pixmap)


def play_icon(size: int = 26) -> QIcon:
    return transport_icon(TransportIconKind.PLAY, size)


def pause_icon(size: int = 26) -> QIcon:
    return transport_icon(TransportIconKind.PAUSE, size)


def seek_start_icon(size: int = 24) -> QIcon:
    return transport_icon(TransportIconKind.SEEK_START, size)


def seek_end_icon(size: int = 24) -> QIcon:
    return transport_icon(TransportIconKind.SEEK_END, size)


def frame_back_icon(size: int = 24) -> QIcon:
    return transport_icon(TransportIconKind.FRAME_BACK, size)


def frame_fwd_icon(size: int = 24) -> QIcon:
    return transport_icon(TransportIconKind.FRAME_FWD, size)


def fullscreen_enter_icon(size: int = 24) -> QIcon:
    return transport_icon(TransportIconKind.FULLSCREEN_ENTER, size)


def fullscreen_exit_icon(size: int = 24) -> QIcon:
    return transport_icon(TransportIconKind.FULLSCREEN_EXIT, size)


def volume_icon(size: int = 22, *, muted: bool = False) -> QIcon:
    kind = TransportIconKind.VOLUME_MUTED if muted else TransportIconKind.VOLUME
    return transport_icon(kind, size)


def volume_pixmap(size: int = 22, *, muted: bool = False) -> QPixmap:
    return volume_icon(size, muted=muted).pixmap(size, size)


def make_transport_button(
    icon: QIcon,
    tooltip: str,
    *,
    checkable: bool = False,
    prominent: bool = False,
    overlay: bool = False,
) -> QToolButton:
    btn = QToolButton()
    btn.setIcon(icon)
    btn.setToolTip(tooltip)
    btn.setAutoRaise(not overlay)
    btn.setIconSize(_TRANSPORT_PLAY_ICON_SIZE if prominent else _TRANSPORT_ICON_SIZE)
    btn.setFixedSize(_TRANSPORT_PLAY_BTN_SIZE if prominent else _TRANSPORT_BTN_SIZE)
    btn.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonIconOnly)
    btn.setStyleSheet(OVERLAY_TRANSPORT_BUTTON_STYLE if overlay else TRANSPORT_BUTTON_STYLE)
    if checkable:
        btn.setCheckable(True)
    return btn


def _stroke_pen() -> QPen:
    return QPen(
        _ICON_COLOR,
        _STROKE,
        Qt.PenStyle.SolidLine,
        Qt.PenCapStyle.RoundCap,
        Qt.PenJoinStyle.RoundJoin,
    )


def _fill_brush() -> QBrush:
    return QBrush(_ICON_COLOR)


def _paint_icon(painter: QPainter, kind: TransportIconKind) -> None:
    painters = {
        TransportIconKind.PLAY: _paint_play,
        TransportIconKind.PAUSE: _paint_pause,
        TransportIconKind.SEEK_START: _paint_seek_start,
        TransportIconKind.SEEK_END: _paint_seek_end,
        TransportIconKind.FRAME_BACK: _paint_frame_back,
        TransportIconKind.FRAME_FWD: _paint_frame_fwd,
        TransportIconKind.FULLSCREEN_ENTER: _paint_fullscreen_enter,
        TransportIconKind.FULLSCREEN_EXIT: _paint_fullscreen_exit,
        TransportIconKind.VOLUME: _paint_volume,
        TransportIconKind.VOLUME_MUTED: _paint_volume_muted,
    }
    painters[kind](painter)


def _paint_play(painter: QPainter) -> None:
    path = QPainterPath()
    path.moveTo(9.0, 6.5)
    path.lineTo(9.0, 17.5)
    path.lineTo(18.5, 12.0)
    path.closeSubpath()
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(_fill_brush())
    painter.drawPath(path)


def _paint_pause(painter: QPainter) -> None:
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(_fill_brush())
    painter.drawRoundedRect(QRectF(7.0, 6.5, 3.5, 11.0), 1.0, 1.0)
    painter.drawRoundedRect(QRectF(13.5, 6.5, 3.5, 11.0), 1.0, 1.0)


def _paint_seek_start(painter: QPainter) -> None:
    painter.setPen(_stroke_pen())
    painter.setBrush(Qt.BrushStyle.NoBrush)
    painter.drawLine(QPointF(6.5, 7.0), QPointF(6.5, 17.0))
    path = QPainterPath()
    path.moveTo(16.5, 7.0)
    path.lineTo(9.0, 12.0)
    path.lineTo(16.5, 17.0)
    path.closeSubpath()
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(_fill_brush())
    painter.drawPath(path)


def _paint_seek_end(painter: QPainter) -> None:
    path = QPainterPath()
    path.moveTo(7.5, 7.0)
    path.lineTo(15.0, 12.0)
    path.lineTo(7.5, 17.0)
    path.closeSubpath()
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(_fill_brush())
    painter.drawPath(path)
    painter.setPen(_stroke_pen())
    painter.setBrush(Qt.BrushStyle.NoBrush)
    painter.drawLine(QPointF(17.5, 7.0), QPointF(17.5, 17.0))


def _paint_frame_back(painter: QPainter) -> None:
    painter.setPen(_stroke_pen())
    painter.setBrush(Qt.BrushStyle.NoBrush)
    painter.drawLine(QPointF(8.0, 7.5), QPointF(8.0, 16.5))
    path = QPainterPath()
    path.moveTo(15.5, 7.5)
    path.lineTo(10.5, 12.0)
    path.lineTo(15.5, 16.5)
    painter.drawPath(path)


def _paint_frame_fwd(painter: QPainter) -> None:
    painter.setPen(_stroke_pen())
    painter.setBrush(Qt.BrushStyle.NoBrush)
    painter.drawLine(QPointF(16.0, 7.5), QPointF(16.0, 16.5))
    path = QPainterPath()
    path.moveTo(8.5, 7.5)
    path.lineTo(13.5, 12.0)
    path.lineTo(8.5, 16.5)
    painter.drawPath(path)


def _paint_fullscreen_enter(painter: QPainter) -> None:
    painter.setPen(_stroke_pen())
    painter.setBrush(Qt.BrushStyle.NoBrush)
    arm = 5.0
    inset = 4.0
    # Top-left
    path = QPainterPath()
    path.moveTo(inset, inset + arm)
    path.lineTo(inset, inset)
    path.lineTo(inset + arm, inset)
    painter.drawPath(path)
    # Top-right
    path = QPainterPath()
    path.moveTo(_CANVAS - inset - arm, inset)
    path.lineTo(_CANVAS - inset, inset)
    path.lineTo(_CANVAS - inset, inset + arm)
    painter.drawPath(path)
    # Bottom-left
    path = QPainterPath()
    path.moveTo(inset, _CANVAS - inset - arm)
    path.lineTo(inset, _CANVAS - inset)
    path.lineTo(inset + arm, _CANVAS - inset)
    painter.drawPath(path)
    # Bottom-right
    path = QPainterPath()
    path.moveTo(_CANVAS - inset - arm, _CANVAS - inset)
    path.lineTo(_CANVAS - inset, _CANVAS - inset)
    path.lineTo(_CANVAS - inset, _CANVAS - inset - arm)
    painter.drawPath(path)


def _paint_fullscreen_exit(painter: QPainter) -> None:
    painter.setPen(_stroke_pen())
    painter.setBrush(Qt.BrushStyle.NoBrush)
    outer = 4.0
    inner = 9.0
    # Top-left — arms point inward
    path = QPainterPath()
    path.moveTo(outer, inner)
    path.lineTo(outer, outer)
    path.lineTo(inner, outer)
    painter.drawPath(path)
    # Top-right
    path = QPainterPath()
    path.moveTo(_CANVAS - inner, outer)
    path.lineTo(_CANVAS - outer, outer)
    path.lineTo(_CANVAS - outer, inner)
    painter.drawPath(path)
    # Bottom-left
    path = QPainterPath()
    path.moveTo(outer, _CANVAS - inner)
    path.lineTo(outer, _CANVAS - outer)
    path.lineTo(inner, _CANVAS - outer)
    painter.drawPath(path)
    # Bottom-right
    path = QPainterPath()
    path.moveTo(_CANVAS - inner, _CANVAS - outer)
    path.lineTo(_CANVAS - outer, _CANVAS - outer)
    path.lineTo(_CANVAS - outer, _CANVAS - inner)
    painter.drawPath(path)


def _paint_volume(painter: QPainter) -> None:
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(_fill_brush())
    # Cabinet + flaring cone as one silhouette so the horn opens to the right.
    speaker = QPainterPath()
    speaker.moveTo(3.5, 9.0)
    speaker.lineTo(7.0, 9.0)
    speaker.lineTo(12.5, 5.25)
    speaker.lineTo(12.5, 18.75)
    speaker.lineTo(7.0, 15.0)
    speaker.lineTo(3.5, 15.0)
    speaker.closeSubpath()
    painter.drawPath(speaker)

    painter.setPen(_stroke_pen())
    painter.setBrush(Qt.BrushStyle.NoBrush)
    # Sound waves facing right (Qt: 0° at 3 o'clock, counter-clockwise).
    painter.drawArc(QRectF(12.25, 7.5, 5.5, 9.0), 310 * 16, 100 * 16)
    painter.drawArc(QRectF(13.75, 5.25, 7.25, 13.5), 310 * 16, 100 * 16)


def _paint_volume_muted(painter: QPainter) -> None:
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(_fill_brush())
    speaker = QPainterPath()
    speaker.moveTo(3.5, 9.0)
    speaker.lineTo(7.0, 9.0)
    speaker.lineTo(12.5, 5.25)
    speaker.lineTo(12.5, 18.75)
    speaker.lineTo(7.0, 15.0)
    speaker.lineTo(3.5, 15.0)
    speaker.closeSubpath()
    painter.drawPath(speaker)

    painter.setPen(_stroke_pen())
    painter.setBrush(Qt.BrushStyle.NoBrush)
    # X over the wave area so mute is obvious at 22px.
    painter.drawLine(QPointF(15.0, 8.0), QPointF(21.0, 16.25))
    painter.drawLine(QPointF(21.0, 8.0), QPointF(15.0, 16.25))
