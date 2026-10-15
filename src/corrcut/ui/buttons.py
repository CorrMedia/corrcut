"""Capsule action buttons.

Quiet pills are the everyday control: a grey fill with a light outline and
label that whiten on hover. Primary pills
are the one solid action on a surface (Generate, dialog OK) and
use the same palette highlight as the enabled toggle in the params panel. A
checked tool uses a lighter fill so the active tool reads without a border.

Qt stylesheets on Windows leave these as rectangles, so the app style paints
the face itself. The corner radius is half the button height.

Icon chrome (transport, snap, row accept/reject) stays on its own styles.
"""

from __future__ import annotations

from PySide6.QtCore import QChildEvent, QEvent, QObject, QRect, QRectF, QSize, Qt
from PySide6.QtGui import QColor, QFont, QPainter, QPalette, QPen
from PySide6.QtWidgets import (
    QAbstractButton,
    QApplication,
    QColorDialog,
    QDialogButtonBox,
    QFileDialog,
    QPushButton,
    QStyle,
    QStyleOption,
    QStyleOptionButton,
    QStyleOptionToolButton,
    QWidget,
)

from corrcut.ui.theme import contrast_text_on

_QUIET = "quiet"
_PRIMARY = "primary"
_installed = False


def is_pill_button(widget: QWidget | None) -> bool:
    return widget is not None and bool(widget.property("pill"))


def use_pill_button(button: QAbstractButton, *, primary: bool = False) -> None:
    """Mark *button* as a quiet pill, or the solid primary pill."""
    button.setProperty("pill", _PRIMARY if primary else _QUIET)
    button.setCursor(Qt.CursorShape.PointingHandCursor)
    button.setAttribute(Qt.WidgetAttribute.WA_Hover, True)
    button.setMinimumHeight(28)
    if primary:
        font = button.font()
        font.setWeight(QFont.Weight.DemiBold)
        button.setFont(font)
    style = button.style()
    style.unpolish(button)
    style.polish(button)
    button.update()


def install_pill_buttons(app: QApplication) -> None:
    """Style dialog OK / Cancel as pills. Faces are painted by ResolveMenuStyle."""
    global _installed
    if _installed:
        return
    _installed = True
    app.installEventFilter(_DialogPillButtons(app))


def draw_pill_push_button(
    style: QStyle,
    option: QStyleOption,
    painter: QPainter,
    widget: QWidget | None,
) -> None:
    del style
    _paint_pill(option, painter, widget)


def draw_pill_tool_button(
    style: QStyle,
    option: QStyleOption,
    painter: QPainter,
    widget: QWidget | None,
) -> None:
    del style
    _paint_pill(option, painter, widget)


def pill_contents_size(size: QSize) -> QSize:
    """Extra width so the label clears the round ends, and a 28px floor."""
    grown = QSize(size)
    grown.setWidth(grown.width() + 16)
    grown.setHeight(max(grown.height(), 28))
    return grown


def _paint_pill(option: QStyleOption, painter: QPainter, widget: QWidget | None) -> None:
    rect = QRectF(option.rect)
    if rect.width() < 4 or rect.height() < 4:
        return
    role = str(widget.property("pill")) if widget is not None else _QUIET
    state = option.state
    palette = widget.palette() if widget is not None else option.palette
    fill, text = _colors(
        role,
        palette,
        enabled=bool(state & QStyle.StateFlag.State_Enabled),
        hover=bool(state & QStyle.StateFlag.State_MouseOver),
        pressed=bool(state & QStyle.StateFlag.State_Sunken),
        checked=bool(state & QStyle.StateFlag.State_On),
        focused=bool(state & QStyle.StateFlag.State_HasFocus),
    )
    checked = bool(state & QStyle.StateFlag.State_On)
    outline = None
    if role != _PRIMARY and not checked:
        outline = _quiet_outline(
            enabled=bool(state & QStyle.StateFlag.State_Enabled),
            hover=bool(state & QStyle.StateFlag.State_MouseOver),
            pressed=bool(state & QStyle.StateFlag.State_Sunken),
            focused=bool(state & QStyle.StateFlag.State_HasFocus),
        )
    painter.save()
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    painter.setBrush(fill)
    face = rect
    if outline is None:
        painter.setPen(Qt.PenStyle.NoPen)
    else:
        painter.setPen(QPen(outline, 1))
        face = rect.adjusted(0.5, 0.5, -0.5, -0.5)
    painter.drawRoundedRect(face, face.height() / 2, face.height() / 2)
    _draw_label(painter, option.rect, option, widget, text)
    painter.restore()


def _draw_label(
    painter: QPainter,
    rect: QRect,
    option: QStyleOption,
    widget: QWidget | None,
    text_color: QColor,
) -> None:
    endcap = max(10, rect.height() // 2 - 4)
    inner = rect.adjusted(endcap, 0, -endcap, 0)
    if inner.width() <= 0:
        return
    text = _option_text(option, widget)
    painter.setPen(text_color)
    painter.setFont(widget.font() if widget is not None else QFont())
    painter.drawText(
        inner,
        int(
            Qt.AlignmentFlag.AlignCenter | Qt.TextFlag.TextShowMnemonic | Qt.TextFlag.TextSingleLine
        ),
        text,
    )


def _option_text(option: QStyleOption, widget: QWidget | None) -> str:
    if isinstance(option, (QStyleOptionButton, QStyleOptionToolButton)):
        return option.text
    if isinstance(widget, QAbstractButton):
        return widget.text()
    return ""


def _colors(
    role: str,
    palette: QPalette,
    *,
    enabled: bool,
    hover: bool,
    pressed: bool,
    checked: bool,
    focused: bool,
) -> tuple[QColor, QColor]:
    """Return fill and text colors."""
    if not enabled:
        if role == _PRIMARY:
            return QColor("#3a3a3a"), QColor("#7a7a7a")
        return QColor("#222222"), QColor("#6a6a6a")
    if role == _PRIMARY:
        return _highlight_face(palette, pressed=pressed, hover=hover, focused=focused)
    if checked:
        if pressed:
            fill = QColor("#2e2e2e")
        elif hover:
            fill = QColor("#4a4a4a")
        else:
            fill = QColor("#3e3e3e")
        return fill, QColor("#f2f2f2")
    if pressed:
        fill = QColor("#1e1e1e")
    elif hover or focused:
        fill = QColor("#313131")
    else:
        fill = QColor("#262626")
    text = _quiet_label(hover=hover, pressed=pressed, focused=focused)
    return fill, text


def _highlight_face(
    palette: QPalette,
    *,
    pressed: bool,
    hover: bool,
    focused: bool,
) -> tuple[QColor, QColor]:
    """Same source as ToggleSwitch when the params-panel enable switch is on."""
    highlight = palette.color(QPalette.ColorRole.Highlight)
    if pressed:
        fill = highlight.darker(125)
    elif hover or focused:
        fill = highlight.lighter(115)
    else:
        fill = QColor(highlight)
    text = contrast_text_on(fill, palette.color(QPalette.ColorRole.HighlightedText))
    return fill, text


def _quiet_label(*, hover: bool, pressed: bool, focused: bool) -> QColor:
    if hover or focused or pressed:
        return QColor("#f2f2f2")
    return QColor("#c4c4c4")


def _quiet_outline(*, enabled: bool, hover: bool, pressed: bool, focused: bool) -> QColor:
    if not enabled:
        return QColor("#4a4a4a")
    if hover or focused or pressed:
        return QColor("#f2f2f2")
    return QColor("#7a7a7a")


class _DialogPillButtons(QObject):
    """Give QDialogButtonBox buttons the pill look, including message boxes."""

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:  # noqa: N802
        if event.type() != QEvent.Type.ChildPolished or not isinstance(event, QChildEvent):
            return False
        if not isinstance(watched, QDialogButtonBox):
            return False
        child = event.child()
        if not isinstance(child, QPushButton) or child.property("pill") or _system_dialog(child):
            return False
        role = watched.buttonRole(child)
        use_pill_button(child, primary=role == QDialogButtonBox.ButtonRole.AcceptRole)
        return False


def _system_dialog(widget: QWidget) -> bool:
    parent = widget.parentWidget()
    while parent is not None:
        if isinstance(parent, (QFileDialog, QColorDialog)):
            return True
        parent = parent.parentWidget()
    return False
