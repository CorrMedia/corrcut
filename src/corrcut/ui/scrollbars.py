"""Scrollbars that stay a fixed size, with no arrow buttons.

Windows 11 shrinks the thumb to a hairline when the pointer leaves, then
draws caret buttons at both ends while the bar is expanded. The app paints
a steady thumb on a steady track instead, and the line buttons have no rect.
"""

from __future__ import annotations

from PySide6.QtCore import QRect, QRectF, Qt
from PySide6.QtGui import QColor, QPainter, QPalette
from PySide6.QtWidgets import QStyle, QStyleOption, QStyleOptionSlider, QWidget

from corrcut.ui.theme import is_dark_color

# Cross-axis size of the bar. Windows 11 uses 12.
SCROLLBAR_EXTENT = 10
# Inset from the bar edge so the thumb reads as a bar, not a filled gutter.
THUMB_INSET = 2

_DARK_TRACK = QColor("#2c2c2c")
_DARK_THUMB = QColor("#6e6e6e")
_DARK_THUMB_HOVER = QColor("#8a8a8a")
_DARK_THUMB_PRESSED = QColor("#9c9c9c")
_DARK_THUMB_DISABLED = QColor("#454545")

_LIGHT_TRACK = QColor("#e4e4e4")
_LIGHT_THUMB = QColor("#9a9a9a")
_LIGHT_THUMB_HOVER = QColor("#707070")
_LIGHT_THUMB_PRESSED = QColor("#5a5a5a")
_LIGHT_THUMB_DISABLED = QColor("#c8c8c8")

_LINE_BUTTONS = {
    QStyle.SubControl.SC_ScrollBarAddLine,
    QStyle.SubControl.SC_ScrollBarSubLine,
    QStyle.SubControl.SC_ScrollBarFirst,
    QStyle.SubControl.SC_ScrollBarLast,
}


def scrollbar_sub_control_rect(
    style: QStyle,
    option: QStyleOption,
    sub_control: QStyle.SubControl,
    widget: QWidget | None,
) -> QRect | None:
    """Scrollbar geometry with the end buttons removed.

    Returns None when *option* is not a scrollbar, so the caller can fall
    through to the base style.
    """
    if not isinstance(option, QStyleOptionSlider):
        return None
    if sub_control in _LINE_BUTTONS:
        return QRect()

    horizontal = option.orientation == Qt.Orientation.Horizontal
    length = option.rect.width() if horizontal else option.rect.height()
    span = int(option.maximum) - int(option.minimum)
    if span > 0:
        page = int(option.pageStep)
        denom = span + page
        slider_len = (page * length) // denom if denom else 0
        slider_min = style.pixelMetric(QStyle.PixelMetric.PM_ScrollBarSliderMin, option, widget)
        if slider_len < slider_min or span > 2**30:
            slider_len = slider_min
        slider_len = min(slider_len, length)
    else:
        slider_len = length

    slider_start = QStyle.sliderPositionFromValue(
        int(option.minimum),
        int(option.maximum),
        int(option.sliderPosition),
        max(0, length - slider_len),
        bool(option.upsideDown),
    )
    origin_x = option.rect.x()
    origin_y = option.rect.y()
    cross = option.rect.height() if horizontal else option.rect.width()
    if horizontal:
        sub_page = QRect(origin_x, origin_y, slider_start, cross)
        add_page = QRect(
            origin_x + slider_start + slider_len,
            origin_y,
            max(0, length - slider_start - slider_len),
            cross,
        )
        slider = QRect(origin_x + slider_start, origin_y, slider_len, cross)
    else:
        sub_page = QRect(origin_x, origin_y, cross, slider_start)
        add_page = QRect(
            origin_x,
            origin_y + slider_start + slider_len,
            cross,
            max(0, length - slider_start - slider_len),
        )
        slider = QRect(origin_x, origin_y + slider_start, cross, slider_len)

    if sub_control == QStyle.SubControl.SC_ScrollBarSubPage:
        rect = sub_page
    elif sub_control == QStyle.SubControl.SC_ScrollBarAddPage:
        rect = add_page
    elif sub_control == QStyle.SubControl.SC_ScrollBarSlider:
        rect = slider
    elif sub_control == QStyle.SubControl.SC_ScrollBarGroove:
        rect = QRect(option.rect)
    else:
        return QRect()
    return style.visualRect(option.direction, option.rect, rect)


def draw_scrollbar(
    style: QStyle,
    option: QStyleOptionSlider,
    painter: QPainter,
    widget: QWidget | None,
) -> None:
    """Paint a track and thumb that keep their size when the pointer leaves."""
    dark = is_dark_color(option.palette.color(QPalette.ColorRole.Window))
    track = _DARK_TRACK if dark else _LIGHT_TRACK
    thumb_color = _thumb_color(option, dark=dark)
    painter.save()
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, False)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(track)
    painter.drawRect(option.rect)

    slider = scrollbar_sub_control_rect(style, option, QStyle.SubControl.SC_ScrollBarSlider, widget)
    if slider is not None and slider.isValid():
        thumb = _thumb_rect(slider, option.orientation == Qt.Orientation.Horizontal)
        if thumb.isValid():
            radius = min(3.0, thumb.width() / 2.0, thumb.height() / 2.0)
            painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            painter.setBrush(thumb_color)
            painter.drawRoundedRect(QRectF(thumb), radius, radius)
    painter.restore()


def _thumb_color(option: QStyleOptionSlider, *, dark: bool) -> QColor:
    enabled = bool(option.state & QStyle.StateFlag.State_Enabled)
    if not enabled:
        return _DARK_THUMB_DISABLED if dark else _LIGHT_THUMB_DISABLED
    if option.state & QStyle.StateFlag.State_Sunken:
        return _DARK_THUMB_PRESSED if dark else _LIGHT_THUMB_PRESSED
    if option.state & QStyle.StateFlag.State_MouseOver:
        return _DARK_THUMB_HOVER if dark else _LIGHT_THUMB_HOVER
    return _DARK_THUMB if dark else _LIGHT_THUMB


def _thumb_rect(slider: QRect, horizontal: bool) -> QRect:
    if horizontal:
        return slider.adjusted(0, THUMB_INSET, 0, -THUMB_INSET)
    return slider.adjusted(THUMB_INSET, 0, -THUMB_INSET, 0)
