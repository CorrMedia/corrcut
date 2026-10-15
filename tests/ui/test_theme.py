"""Dark-background text contrast helpers."""

from __future__ import annotations

from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QApplication, QLabel, QWidget

from corrcut.ui.theme import (
    apply_muted_text,
    contrast_text_on,
    file_dialog_contrast_stylesheet,
    list_hover_color,
    muted_text_on,
)

_app = QApplication.instance() or QApplication([])


def _dark_palette() -> QPalette:
    palette = QPalette()
    palette.setColor(QPalette.ColorRole.Window, QColor("#202020"))
    palette.setColor(QPalette.ColorRole.WindowText, QColor("#f2f2f2"))
    palette.setColor(QPalette.ColorRole.Base, QColor("#1a1a1a"))
    palette.setColor(QPalette.ColorRole.Text, QColor("#f2f2f2"))
    palette.setColor(QPalette.ColorRole.Button, QColor("#2c2c2c"))
    palette.setColor(QPalette.ColorRole.ButtonText, QColor("#f2f2f2"))
    palette.setColor(QPalette.ColorRole.Mid, QColor("#333333"))
    palette.setColor(QPalette.ColorRole.Highlight, QColor("#3d6ea8"))
    palette.setColor(QPalette.ColorRole.HighlightedText, QColor("#ffffff"))
    return palette


def test_muted_text_is_lighter_than_mid_on_dark_window() -> None:
    window = QColor("#202020")
    mid = QColor("#333333")
    muted = muted_text_on(window, QColor("#f2f2f2"))
    assert muted.lightness() > mid.lightness()
    assert abs(muted.lightness() - window.lightness()) >= 80


def test_list_hover_color_stays_neutral_and_apart_from_highlight() -> None:
    highlight = QColor("#3d6ea8")
    dark_hover = list_hover_color(QColor("#202020"), highlight)
    light_hover = list_hover_color(QColor("#ffffff"), highlight)
    for hover in (dark_hover, light_hover):
        assert hover.hslSaturation() < 20
        distance = (
            abs(hover.red() - highlight.red())
            + abs(hover.green() - highlight.green())
            + abs(hover.blue() - highlight.blue())
        )
        assert distance >= 64
    assert dark_hover.lightness() > QColor("#202020").lightness()
    assert light_hover.lightness() < QColor("#ffffff").lightness()


def test_contrast_text_overrides_dark_foreground_on_dark_background() -> None:
    text = contrast_text_on(QColor("#202020"), QColor("#111111"))
    assert text.lightness() > 180


def test_apply_muted_text_does_not_use_palette_mid() -> None:
    host = QWidget()
    host.setPalette(_dark_palette())
    label = QLabel("Open a recent project or start a new one.", host)
    label.setPalette(_dark_palette())
    apply_muted_text(label, extra_css="font-style: italic;")
    css = label.styleSheet()
    assert "palette(mid)" not in css
    assert "font-style: italic;" in css
    assert "#333333" not in css.lower()


def test_file_dialog_stylesheet_forces_light_text_when_palette_text_is_dark() -> None:
    palette = _dark_palette()
    palette.setColor(QPalette.ColorRole.WindowText, QColor("#111111"))
    palette.setColor(QPalette.ColorRole.Text, QColor("#111111"))
    css = file_dialog_contrast_stylesheet(palette)
    assert "#111111" not in css.lower()
    assert "color: #e8e8e8;" in css.lower()
