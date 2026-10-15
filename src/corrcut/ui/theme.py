"""Readable colors for dark (and light) Qt palettes."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QApplication, QFileDialog, QWidget

_DARK_TEXT = QColor("#e8e8e8")
_LIGHT_TEXT = QColor("#1a1a1a")
_MIN_CONTRAST_LIGHTNESS = 100


def is_dark_color(color: QColor) -> bool:
    return color.lightness() < 128


def contrast_text_on(background: QColor, preferred: QColor | None = None) -> QColor:
    """Return a foreground that stays readable on *background*."""
    if preferred is not None and (
        abs(preferred.lightness() - background.lightness()) >= _MIN_CONTRAST_LIGHTNESS
    ):
        return preferred
    return _DARK_TEXT if is_dark_color(background) else _LIGHT_TEXT


def _neutral(lightness: int) -> QColor:
    return QColor.fromHsl(0, 0, min(255, max(0, lightness)))


def _color_distance(left: QColor, right: QColor) -> int:
    return (
        abs(left.red() - right.red())
        + abs(left.green() - right.green())
        + abs(left.blue() - right.blue())
    )


def list_hover_color(base: QColor, highlight: QColor) -> QColor:
    """Neutral row hover, kept apart from the accent used for selection."""
    adjusted = base.lighter(170) if is_dark_color(base) else base.darker(112)
    hover = _neutral(adjusted.lightness())
    if _color_distance(hover, highlight) >= 64:
        return hover
    for delta in (48, -48, 80, -80):
        candidate = _neutral(hover.lightness() + delta)
        if _color_distance(candidate, highlight) >= 64:
            return candidate
    return hover


def muted_text_on(background: QColor, preferred: QColor | None = None) -> QColor:
    """Secondary text: blend toward the background without collapsing contrast."""
    text = contrast_text_on(background, preferred)
    return QColor(
        (text.red() * 3 + background.red()) // 4,
        (text.green() * 3 + background.green()) // 4,
        (text.blue() * 3 + background.blue()) // 4,
    )


def apply_muted_text(widget: QWidget, extra_css: str = "") -> None:
    palette = widget.palette()
    window = palette.color(QPalette.ColorRole.Window)
    preferred = palette.color(QPalette.ColorRole.WindowText)
    color = muted_text_on(window, preferred)
    suffix = f" {extra_css}" if extra_css else ""
    widget.setStyleSheet(f"color: {color.name()};{suffix}")


def file_dialog_contrast_stylesheet(palette: QPalette) -> str:
    window = palette.color(QPalette.ColorRole.Window)
    base = palette.color(QPalette.ColorRole.Base)
    button = palette.color(QPalette.ColorRole.Button)
    text = contrast_text_on(window, palette.color(QPalette.ColorRole.WindowText))
    base_text = contrast_text_on(base, palette.color(QPalette.ColorRole.Text))
    button_text = contrast_text_on(button, palette.color(QPalette.ColorRole.ButtonText))
    highlight = palette.color(QPalette.ColorRole.Highlight)
    highlighted_text = contrast_text_on(
        highlight, palette.color(QPalette.ColorRole.HighlightedText)
    )
    return f"""
        QFileDialog {{
            background-color: {window.name()};
            color: {text.name()};
        }}
        QFileDialog QLabel, QFileDialog QCheckBox, QFileDialog QToolButton {{
            color: {text.name()};
        }}
        QFileDialog QLineEdit, QFileDialog QComboBox,
        QFileDialog QComboBox QAbstractItemView {{
            color: {base_text.name()};
            background-color: {base.name()};
        }}
        QFileDialog QTreeView, QFileDialog QListView {{
            color: {base_text.name()};
            background-color: {base.name()};
        }}
        QFileDialog QTreeView::item, QFileDialog QListView::item {{
            color: {base_text.name()};
        }}
        QFileDialog QTreeView::item:selected, QFileDialog QListView::item:selected {{
            color: {highlighted_text.name()};
            background-color: {highlight.name()};
        }}
        QFileDialog QPushButton {{
            color: {button_text.name()};
            background-color: {button.name()};
        }}
        QFileDialog QHeaderView::section {{
            color: {text.name()};
            background-color: {window.name()};
        }}
    """


def apply_file_dialog_contrast(dialog: QFileDialog) -> None:
    app = QApplication.instance()
    palette = app.palette() if app is not None else dialog.palette()
    dialog.setPalette(palette)
    dialog.setStyleSheet(file_dialog_contrast_stylesheet(palette))


def choose_open_path(
    parent: QWidget | None,
    title: str,
    file_filter: str,
    *,
    initial_dir: str = "",
    options: QFileDialog.Option | None = None,
) -> Path | None:
    dialog = QFileDialog(parent, title, initial_dir, file_filter)
    dialog.setFileMode(QFileDialog.FileMode.ExistingFile)
    dialog.setAcceptMode(QFileDialog.AcceptMode.AcceptOpen)
    if options is not None:
        dialog.setOptions(options)
    apply_file_dialog_contrast(dialog)
    if dialog.exec() != QFileDialog.DialogCode.Accepted:
        return None
    selected = dialog.selectedFiles()
    return Path(selected[0]) if selected else None


def choose_save_path(
    parent: QWidget | None,
    title: str,
    file_filter: str,
    *,
    initial: str = "",
    options: QFileDialog.Option | None = None,
) -> Path | None:
    dialog = QFileDialog(parent, title, initial, file_filter)
    dialog.setAcceptMode(QFileDialog.AcceptMode.AcceptSave)
    dialog.setFileMode(QFileDialog.FileMode.AnyFile)
    if options is not None:
        dialog.setOptions(options)
    apply_file_dialog_contrast(dialog)
    if dialog.exec() != QFileDialog.DialogCode.Accepted:
        return None
    selected = dialog.selectedFiles()
    return Path(selected[0]) if selected else None
