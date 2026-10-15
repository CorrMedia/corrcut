"""Scrollbars stay wide when idle and have no end buttons."""

from __future__ import annotations

from PySide6.QtCore import QRect, Qt
from PySide6.QtGui import QColor, QImage, QPainter, QPalette
from PySide6.QtWidgets import QApplication, QStyle, QStyleOptionSlider

from corrcut.ui.scrollbars import SCROLLBAR_EXTENT, _DARK_TRACK
from corrcut.ui.square_menus import ResolveMenuStyle

_app = QApplication.instance() or QApplication([])


def _vertical_option(*, mouse_over: bool = False) -> QStyleOptionSlider:
    option = QStyleOptionSlider()
    option.rect = QRect(0, 0, SCROLLBAR_EXTENT, 200)
    option.orientation = Qt.Orientation.Vertical
    option.minimum = 0
    option.maximum = 100
    option.pageStep = 20
    option.sliderPosition = 0
    option.state = QStyle.StateFlag.State_Enabled
    if mouse_over:
        option.state |= QStyle.StateFlag.State_MouseOver
    palette = option.palette
    palette.setColor(QPalette.ColorRole.Window, QColor("#1e1e1e"))
    option.palette = palette
    return option


def _thumb_run(image: QImage, y: int) -> int:
    """Pixels on this row that are the thumb, not the track."""
    count = 0
    for x in range(image.width()):
        color = image.pixelColor(x, y)
        if color.alpha() > 200 and abs(color.red() - _DARK_TRACK.red()) > 12:
            count += 1
    return count


def test_scrollbar_extent_is_two_pixels_under_windows_11() -> None:
    style = ResolveMenuStyle(_app.style().objectName() or "Fusion")
    assert style.pixelMetric(QStyle.PixelMetric.PM_ScrollBarExtent) == SCROLLBAR_EXTENT
    assert SCROLLBAR_EXTENT == 10


def test_scrollbar_end_buttons_have_no_rect() -> None:
    style = ResolveMenuStyle(_app.style().objectName() or "Fusion")
    option = _vertical_option()
    for sub in (
        QStyle.SubControl.SC_ScrollBarAddLine,
        QStyle.SubControl.SC_ScrollBarSubLine,
    ):
        rect = style.subControlRect(QStyle.ComplexControl.CC_ScrollBar, option, sub, None)
        assert rect.isNull()


def test_scrollbar_thumb_starts_at_the_track_edge() -> None:
    style = ResolveMenuStyle(_app.style().objectName() or "Fusion")
    option = _vertical_option()
    slider = style.subControlRect(
        QStyle.ComplexControl.CC_ScrollBar,
        option,
        QStyle.SubControl.SC_ScrollBarSlider,
        None,
    )
    assert slider.top() == option.rect.top()
    assert slider.left() == option.rect.left()
    assert slider.width() == option.rect.width()


def test_idle_scrollbar_thumb_stays_wide() -> None:
    style = ResolveMenuStyle(_app.style().objectName() or "Fusion")
    image = QImage(SCROLLBAR_EXTENT, 200, QImage.Format.Format_ARGB32)
    image.fill(QColor("#ff00ff"))
    painter = QPainter(image)
    style.drawComplexControl(
        QStyle.ComplexControl.CC_ScrollBar, _vertical_option(), painter, None
    )
    painter.end()

    # A collapsed Windows 11 thumb is a 2px hairline. This one stays wide.
    assert _thumb_run(image, 10) >= 6
    track = image.pixelColor(0, 10)
    assert abs(track.red() - _DARK_TRACK.red()) < 8
    # The far end of the track is still painted when the thumb is at the start.
    far = image.pixelColor(6, 180)
    assert abs(far.red() - _DARK_TRACK.red()) < 8


def test_hover_does_not_change_thumb_width() -> None:
    style = ResolveMenuStyle(_app.style().objectName() or "Fusion")

    def paint(mouse_over: bool) -> QImage:
        image = QImage(SCROLLBAR_EXTENT, 200, QImage.Format.Format_ARGB32)
        image.fill(QColor("#ff00ff"))
        painter = QPainter(image)
        style.drawComplexControl(
            QStyle.ComplexControl.CC_ScrollBar,
            _vertical_option(mouse_over=mouse_over),
            painter,
            None,
        )
        painter.end()
        return image

    assert _thumb_run(paint(False), 10) == _thumb_run(paint(True), 10)
