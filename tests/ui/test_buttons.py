"""Pill action-button roles."""

from __future__ import annotations

from PySide6.QtGui import QColor, QImage, QPainter, QPalette
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QDialogButtonBox,
    QPushButton,
    QStyle,
    QStyleOptionButton,
    QToolButton,
    QVBoxLayout,
)

from corrcut.ui.buttons import draw_pill_push_button, install_pill_buttons, use_pill_button
from corrcut.ui.toggle_switch import ToggleSwitch

_app = QApplication.instance() or QApplication([])


def test_pill_face_is_a_capsule() -> None:
    button = QPushButton("Open project")
    use_pill_button(button)
    button.resize(160, 28)
    image = QImage(button.size(), QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(0)
    painter = QPainter(image)
    option = QStyleOptionButton()
    button.initStyleOption(option)
    option.rect = button.rect()
    draw_pill_push_button(button.style(), option, painter, button)
    painter.end()

    corner = image.pixelColor(0, 0)
    # Inside the left cap, clear of the label.
    fill = image.pixelColor(16, button.height() // 2)
    assert corner.alpha() < 20
    assert fill.alpha() > 200
    assert fill.red() < 80


def _paint_quiet_pill(*, hover: bool) -> QImage:
    button = QPushButton("Browse")
    use_pill_button(button)
    button.resize(160, 28)
    image = QImage(button.size(), QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(0)
    painter = QPainter(image)
    option = QStyleOptionButton()
    button.initStyleOption(option)
    option.rect = button.rect()
    if hover:
        option.state |= QStyle.StateFlag.State_MouseOver
    draw_pill_push_button(button.style(), option, painter, button)
    painter.end()
    return image


def _top_stroke(image: QImage) -> QColor:
    x = image.width() // 2
    for y in range(image.height()):
        color = image.pixelColor(x, y)
        if color.alpha() > 160:
            return color
    return QColor()


def _label_ink(image: QImage) -> QColor:
    lightest = QColor("#000000")
    for y in range(8, image.height() - 8):
        for x in range(36, image.width() - 36):
            color = image.pixelColor(x, y)
            if color.lightness() > lightest.lightness():
                lightest = color
    return lightest


def test_quiet_pill_outline_and_label_whiten_on_hover() -> None:
    idle_image = _paint_quiet_pill(hover=False)
    hover_image = _paint_quiet_pill(hover=True)
    idle = _top_stroke(idle_image)
    hovered = _top_stroke(hover_image)
    assert idle.lightness() > 90
    assert hovered.lightness() > idle.lightness() + 40
    idle_text = _label_ink(idle_image)
    hover_text = _label_ink(hover_image)
    assert idle_text.lightness() > 90
    assert hover_text.lightness() > idle_text.lightness() + 40


def test_primary_pill_matches_enabled_params_toggle() -> None:
    highlight = QColor("#3d6ea8")
    button = QPushButton("New project")
    use_pill_button(button, primary=True)
    palette = button.palette()
    palette.setColor(QPalette.ColorRole.Highlight, highlight)
    button.setPalette(palette)
    toggle = ToggleSwitch()
    toggle.setPalette(palette)
    assert toggle.palette().color(QPalette.ColorRole.Highlight) == highlight

    button.resize(160, 28)
    image = QImage(button.size(), QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(0)
    painter = QPainter(image)
    option = QStyleOptionButton()
    button.initStyleOption(option)
    option.rect = button.rect()
    draw_pill_push_button(button.style(), option, painter, button)
    painter.end()

    fill = image.pixelColor(16, button.height() // 2)
    assert abs(fill.red() - highlight.red()) < 8
    assert abs(fill.green() - highlight.green()) < 8
    assert abs(fill.blue() - highlight.blue()) < 8


def test_use_pill_button_marks_quiet_and_primary() -> None:
    quiet = QPushButton("Open project")
    primary = QPushButton("New project")
    tool = QToolButton()
    use_pill_button(quiet)
    use_pill_button(primary, primary=True)
    use_pill_button(tool)
    assert quiet.property("pill") == "quiet"
    assert primary.property("pill") == "primary"
    assert tool.property("pill") == "quiet"


def test_dialog_accept_button_is_the_primary_pill() -> None:
    install_pill_buttons(_app)
    dialog = QDialog()
    box = QDialogButtonBox(
        QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
    )
    layout = QVBoxLayout(dialog)
    layout.addWidget(box)
    dialog.show()
    _app.processEvents()

    ok = box.button(QDialogButtonBox.StandardButton.Ok)
    cancel = box.button(QDialogButtonBox.StandardButton.Cancel)
    assert ok is not None and ok.property("pill") == "primary"
    assert cancel is not None and cancel.property("pill") == "quiet"
    dialog.close()
