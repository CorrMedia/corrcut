"""DaVinci Resolve menu look: flat dark panels and square item highlights.

Windows 11 rounds the menu window during Show, and the Qt style paints a
rounded hover inside each item. The window corner is cleared on the next
turn so the menu can finish opening. Item and panel painting is square.
"""

from __future__ import annotations

import sys
from typing import cast

from PySide6.QtCore import QEvent, QObject, QPoint, QRect, QSize, Qt, QTimer
from PySide6.QtGui import QColor, QIcon, QPainter, QPalette, QPen, QPolygon
from PySide6.QtWidgets import (
    QApplication,
    QMenu,
    QProxyStyle,
    QStyle,
    QStyleFactory,
    QStyleHintReturn,
    QStyleOption,
    QStyleOptionComplex,
    QStyleOptionMenuItem,
    QStyleOptionSlider,
    QStyleOptionTab,
    QStyleOptionViewItem,
    QTabBar,
    QWidget,
)

from corrcut.ui.buttons import (
    draw_pill_push_button,
    draw_pill_tool_button,
    is_pill_button,
    pill_contents_size,
)
from corrcut.ui.scrollbars import (
    SCROLLBAR_EXTENT,
    draw_scrollbar,
    scrollbar_sub_control_rect,
)
from corrcut.ui.theme import list_hover_color

MENU_PANEL = QColor("#2b2b2b")
MENU_BORDER = QColor("#101010")
MENU_HIGHLIGHT = QColor("#4a4a4a")
MENU_TEXT = QColor("#e8e8e8")
MENU_SHORTCUT = QColor("#9a9a9a")
MENU_DISABLED = QColor("#6e6e6e")
MENU_SEPARATOR = QColor("#141414")

RECENT_PROJECT_LIST_PROPERTY = "recentProjectList"

_DWMWA_WINDOW_CORNER_PREFERENCE = 33
_DWMWCP_DONOTROUND = 1

_installed: _SquareMenuCorners | None = None


def install_square_menu_corners(app: QApplication) -> None:
    """Use Resolve-style menus. Square window corners on Windows."""
    global _installed
    if _installed is not None:
        return
    app.setStyle(ResolveMenuStyle(_style_key(app)))
    _installed = _SquareMenuCorners(app)
    if sys.platform == "win32":
        app.installEventFilter(_installed)


def _is_recent_project_list(widget: QWidget | None) -> bool:
    return widget is not None and bool(widget.property(RECENT_PROJECT_LIST_PROPERTY))


def _recent_project_option(option: QStyleOption, widget: QWidget | None) -> bool:
    return isinstance(option, QStyleOptionViewItem) and _is_recent_project_list(widget)


def _paint_recent_project_panel(
    option: QStyleOption,
    painter: QPainter,
    widget: QWidget | None,
) -> bool:
    """Replace the shared Windows hover/selection fill. Leave text to the style."""
    if not _recent_project_option(option, widget):
        return False
    assert isinstance(option, QStyleOptionViewItem)
    selected = bool(option.state & QStyle.StateFlag.State_Selected)
    hovered = bool(option.state & QStyle.StateFlag.State_MouseOver)
    if not selected and not hovered:
        return False
    highlight = option.palette.color(QPalette.ColorRole.Highlight)
    fill = (
        highlight
        if selected
        else list_hover_color(option.palette.color(QPalette.ColorRole.Base), highlight)
    )
    painter.fillRect(option.rect, fill)
    return True


def _draw_recent_project_item(
    style: QProxyStyle,
    option: QStyleOption,
    painter: QPainter,
    widget: QWidget | None,
) -> None:
    assert isinstance(option, QStyleOptionViewItem)
    selected = bool(option.state & QStyle.StateFlag.State_Selected)
    hovered = bool(option.state & QStyle.StateFlag.State_MouseOver)
    if hovered and not selected:
        opt = QStyleOptionViewItem(option)
        opt.palette.setColor(
            QPalette.ColorRole.HighlightedText,
            opt.palette.color(QPalette.ColorRole.Text),
        )
        option = opt
    # Skip ResolveMenuStyle.drawControl so this does not recurse. The base
    # style draws the label, and asks us back for the row fill.
    QProxyStyle.drawControl(
        style,
        QStyle.ControlElement.CE_ItemViewItem,
        option,
        painter,
        widget,
    )


class ResolveMenuStyle(QProxyStyle):
    """Windows 11 widgets, with menu chrome painted as hard rectangles.

    Scrollbars keep a steady thumb. They do not collapse when idle, and they
    have no arrow buttons at the ends.
    """

    def __init__(self, key: str) -> None:
        super().__init__(key)

    def polish(self, widget):  # type: ignore[no-untyped-def]
        super().polish(widget)
        if not isinstance(widget, QMenu):
            return
        widget.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, False)
        widget.setGraphicsEffect(None)
        palette = widget.palette()
        palette.setColor(QPalette.ColorRole.Window, MENU_PANEL)
        palette.setColor(QPalette.ColorRole.Base, MENU_PANEL)
        palette.setColor(QPalette.ColorRole.Text, MENU_TEXT)
        palette.setColor(QPalette.ColorRole.WindowText, MENU_TEXT)
        palette.setColor(QPalette.ColorRole.ButtonText, MENU_TEXT)
        palette.setColor(QPalette.ColorRole.Highlight, MENU_HIGHLIGHT)
        palette.setColor(QPalette.ColorRole.HighlightedText, MENU_TEXT)
        widget.setPalette(palette)
        widget.setAutoFillBackground(True)

    def drawPrimitive(  # noqa: N802
        self,
        element: QStyle.PrimitiveElement,
        option: QStyleOption,
        painter: QPainter,
        widget=None,  # type: ignore[no-untyped-def]
    ) -> None:
        if element == QStyle.PrimitiveElement.PE_PanelMenu:
            _paint_menu_panel(painter, option.rect)
            return
        if (
            element == QStyle.PrimitiveElement.PE_PanelItemViewItem
            and _paint_recent_project_panel(option, painter, widget)
        ):
            return
        super().drawPrimitive(element, option, painter, widget)

    def drawControl(  # noqa: N802
        self,
        element: QStyle.ControlElement,
        option: QStyleOption,
        painter: QPainter,
        widget=None,  # type: ignore[no-untyped-def]
    ) -> None:
        if element == QStyle.ControlElement.CE_ItemViewItem and _recent_project_option(
            option, widget
        ):
            _draw_recent_project_item(self, option, painter, widget)
            return
        if element == QStyle.ControlElement.CE_PushButton and is_pill_button(widget):
            draw_pill_push_button(self, option, painter, widget)
            return
        if (
            element == QStyle.ControlElement.CE_MenuItem
            and isinstance(option, QStyleOptionMenuItem)
        ):
            _paint_menu_item(self, option, painter, widget)
            return
        if (
            element == QStyle.ControlElement.CE_MenuBarItem
            and isinstance(option, QStyleOptionMenuItem)
        ):
            _paint_menu_bar_item(self, option, painter, widget)
            return
        if (
            isinstance(option, QStyleOptionTab)
            and option.shape in _BOTTOM_TAB_SHAPES
            and element
            in {
                QStyle.ControlElement.CE_TabBarTab,
                QStyle.ControlElement.CE_TabBarTabShape,
            }
        ):
            # Windows 11 paints a bottom tab with the top-tab face, and it draws
            # the whole tab without asking the proxy for the shape separately.
            if element == QStyle.ControlElement.CE_TabBarTab:
                self.drawControl(
                    QStyle.ControlElement.CE_TabBarTabShape, option, painter, widget
                )
                self.drawControl(
                    QStyle.ControlElement.CE_TabBarTabLabel, option, painter, widget
                )
                return
            north = QStyleOptionTab(option)
            north.shape = _BOTTOM_TAB_SHAPES[option.shape]
            painter.save()
            _flip_vertical(painter, option.rect)
            super().drawControl(element, north, painter, None)
            painter.restore()
            return
        super().drawControl(element, option, painter, widget)

    def drawComplexControl(  # noqa: N802
        self,
        control: QStyle.ComplexControl,
        option: QStyleOptionComplex,
        painter: QPainter,
        widget: QWidget | None = None,
    ) -> None:
        if control == QStyle.ComplexControl.CC_ToolButton and is_pill_button(widget):
            draw_pill_tool_button(self, option, painter, widget)
            return
        if control == QStyle.ComplexControl.CC_ScrollBar and isinstance(option, QStyleOptionSlider):
            draw_scrollbar(self, option, painter, widget)
            return
        super().drawComplexControl(control, option, painter, widget)

    def subControlRect(  # noqa: N802
        self,
        control: QStyle.ComplexControl,
        option: QStyleOptionComplex,
        sub_control: QStyle.SubControl,
        widget: QWidget | None = None,
    ) -> QRect:
        if control == QStyle.ComplexControl.CC_ScrollBar:
            rect = scrollbar_sub_control_rect(self, option, sub_control, widget)
            if rect is not None:
                return rect
        return super().subControlRect(control, option, sub_control, cast(QWidget, widget))

    def styleHint(  # noqa: N802
        self,
        hint: QStyle.StyleHint,
        option: QStyleOption | None = None,
        widget: QWidget | None = None,
        returnData: QStyleHintReturn | None = None,
    ) -> int:
        # Transient bars fade out when idle. Keep the track on screen.
        if hint == QStyle.StyleHint.SH_ScrollBar_Transient:
            return 0
        return super().styleHint(hint, option, widget, returnData)

    def sizeFromContents(  # noqa: N802
        self,
        contents_type: QStyle.ContentsType,
        option: QStyleOption,
        contents_size: QSize,
        widget: QWidget | None = None,
    ) -> QSize:
        size = super().sizeFromContents(
            contents_type, option, contents_size, cast(QWidget, widget)
        )
        if is_pill_button(widget) and contents_type in {
            QStyle.ContentsType.CT_PushButton,
            QStyle.ContentsType.CT_ToolButton,
        }:
            return pill_contents_size(size)
        return size

    def pixelMetric(  # noqa: N802
        self,
        metric: QStyle.PixelMetric,
        option: QStyleOption | None = None,
        widget=None,  # type: ignore[no-untyped-def]
    ) -> int:
        if metric == QStyle.PixelMetric.PM_MenuPanelWidth:
            return 1
        if metric == QStyle.PixelMetric.PM_MenuHMargin:
            return 0
        if metric == QStyle.PixelMetric.PM_MenuVMargin:
            return 4
        if metric == QStyle.PixelMetric.PM_ScrollBarExtent:
            return SCROLLBAR_EXTENT
        return super().pixelMetric(metric, option, widget)


class _SquareMenuCorners(QObject):
    def eventFilter(self, watched: QObject, event: QEvent) -> bool:  # noqa: N802
        if event.type() == QEvent.Type.Show and isinstance(watched, QMenu):
            QTimer.singleShot(0, lambda menu=watched: _dont_round(menu))
        return False


def menu_corner_preference(menu: QMenu) -> int | None:
    """DWM corner preference for *menu*, or None when it cannot be read."""
    if sys.platform != "win32":
        return None
    import ctypes
    from ctypes import wintypes

    value = ctypes.c_uint(0)
    result = ctypes.windll.dwmapi.DwmGetWindowAttribute(
        wintypes.HWND(int(menu.winId())),
        _DWMWA_WINDOW_CORNER_PREFERENCE,
        ctypes.byref(value),
        ctypes.sizeof(value),
    )
    if result != 0:
        return None
    return int(value.value)


def _style_key(app: QApplication) -> str:
    available = list(QStyleFactory.keys())
    keys = {key.lower(): key for key in available}
    current = app.style().objectName()
    if current and current.lower() in keys:
        return keys[current.lower()]
    for name in ("windows11", "windowsvista", "Windows", "Fusion"):
        if name.lower() in keys:
            return keys[name.lower()]
    return next(iter(keys.values()), "Fusion")


_BOTTOM_TAB_SHAPES = {
    QTabBar.Shape.RoundedSouth: QTabBar.Shape.RoundedNorth,
    QTabBar.Shape.TriangularSouth: QTabBar.Shape.TriangularNorth,
}


def _flip_vertical(painter: QPainter, rect: QRect) -> None:
    """Mirror *rect* top-to-bottom.

    ``top() + height()`` rather than ``bottom()`` keeps the Windows style's
    antialiased curve inside the rect.
    """
    painter.translate(rect.left(), rect.top() + rect.height())
    painter.scale(1, -1)
    painter.translate(-rect.left(), -rect.top())


def _paint_menu_panel(painter: QPainter, rect: QRect) -> None:
    painter.save()
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, False)
    painter.fillRect(rect, MENU_PANEL)
    painter.setPen(QPen(MENU_BORDER, 1))
    painter.drawRect(rect.adjusted(0, 0, -1, -1))
    painter.restore()


def _paint_menu_item(
    style: QStyle,
    option: QStyleOptionMenuItem,
    painter: QPainter,
    widget,
) -> None:
    painter.save()
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, False)
    rect = option.rect
    enabled = bool(option.state & QStyle.StateFlag.State_Enabled)
    selected = enabled and bool(option.state & QStyle.StateFlag.State_Selected)
    if option.menuItemType == QStyleOptionMenuItem.MenuItemType.Separator:
        painter.setPen(MENU_SEPARATOR)
        y = rect.center().y()
        painter.drawLine(rect.left() + 8, y, rect.right() - 8, y)
        painter.restore()
        return
    if selected:
        painter.fillRect(rect, MENU_HIGHLIGHT)

    text_color = MENU_TEXT if enabled else MENU_DISABLED
    painter.setFont(option.font)
    left = rect.left() + 8
    checkable = (
        option.menuHasCheckableItems
        or option.checkType != QStyleOptionMenuItem.CheckType.NotCheckable
    )
    if checkable:
        check_rect = QRect(rect.left() + 4, rect.top(), 16, rect.height())
        if option.checked:
            _draw_check(painter, check_rect, text_color)
        left = check_rect.right() + 4
    if not option.icon.isNull():
        size = style.pixelMetric(QStyle.PixelMetric.PM_SmallIconSize, option, widget)
        icon_rect = QRect(left, rect.center().y() - size // 2, size, size)
        mode = QIcon.Mode.Normal if enabled else QIcon.Mode.Disabled
        option.icon.paint(painter, icon_rect, Qt.AlignmentFlag.AlignCenter, mode)
        left = icon_rect.right() + 6

    is_submenu = option.menuItemType == QStyleOptionMenuItem.MenuItemType.SubMenu
    right_reserve = 16 if is_submenu else max(int(option.reservedShortcutWidth), 0)
    text_width = max(0, rect.right() - 8 - right_reserve - left)
    text_rect = QRect(left, rect.top(), text_width, rect.height())
    label, shortcut = _split_menu_text(option.text)
    painter.setPen(text_color)
    painter.drawText(text_rect, _label_flags(style, option, widget), label)
    if shortcut and not is_submenu and option.reservedShortcutWidth > 0:
        shortcut_rect = QRect(
            rect.right() - int(option.reservedShortcutWidth) - 10,
            rect.top(),
            int(option.reservedShortcutWidth),
            rect.height(),
        )
        painter.setPen(MENU_SHORTCUT if enabled else MENU_DISABLED)
        painter.drawText(
            shortcut_rect,
            int(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight),
            shortcut,
        )
    if is_submenu:
        _draw_submenu_arrow(painter, rect, text_color)
    painter.restore()


def _paint_menu_bar_item(
    style: QStyle,
    option: QStyleOptionMenuItem,
    painter: QPainter,
    widget,
) -> None:
    del widget
    painter.save()
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, False)
    enabled = bool(option.state & QStyle.StateFlag.State_Enabled)
    hot = enabled and bool(
        option.state
        & (
            QStyle.StateFlag.State_Selected
            | QStyle.StateFlag.State_Sunken
            | QStyle.StateFlag.State_MouseOver
        )
    )
    if hot:
        painter.fillRect(option.rect, MENU_PANEL)
        painter.setPen(MENU_TEXT)
    else:
        role = QPalette.ColorRole.WindowText if enabled else QPalette.ColorRole.Mid
        painter.setPen(option.palette.color(role))
    painter.setFont(option.font)
    painter.drawText(option.rect, _label_flags(style, option, None, center=True), option.text)
    painter.restore()


def _split_menu_text(text: str) -> tuple[str, str]:
    label, separator, shortcut = text.partition("\t")
    if not separator:
        return text, ""
    return label, shortcut


def _label_flags(
    style: QStyle,
    option: QStyleOptionMenuItem,
    widget,
    *,
    center: bool = False,
) -> int:
    align = Qt.AlignmentFlag.AlignCenter if center else Qt.AlignmentFlag.AlignLeft
    flags = int(Qt.AlignmentFlag.AlignVCenter | align)
    flags |= int(Qt.TextFlag.TextSingleLine | Qt.TextFlag.TextDontClip)
    show_mnemonic = bool(style.styleHint(QStyle.StyleHint.SH_UnderlineShortcut, option, widget))
    mnemonic = Qt.TextFlag.TextShowMnemonic if show_mnemonic else Qt.TextFlag.TextHideMnemonic
    return flags | int(mnemonic)


def _draw_check(painter: QPainter, rect: QRect, color: QColor) -> None:
    x = rect.center().x() - 3
    y = rect.center().y()
    painter.setPen(QPen(color, 1))
    painter.drawLine(x, y, x + 2, y + 3)
    painter.drawLine(x + 2, y + 3, x + 7, y - 3)


def _draw_submenu_arrow(painter: QPainter, rect: QRect, color: QColor) -> None:
    cy = rect.center().y()
    x = rect.right() - 10
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(color)
    painter.drawPolygon(
        QPolygon([QPoint(x, cy - 3), QPoint(x + 4, cy), QPoint(x, cy + 3)])
    )


def _dont_round(menu: QMenu) -> None:
    from shiboken6 import Shiboken

    if not Shiboken.isValid(menu) or not menu.isVisible():
        return
    _set_corner_preference(int(menu.winId()), _DWMWCP_DONOTROUND)


def _set_corner_preference(hwnd: int, preference: int) -> None:
    import ctypes
    from ctypes import wintypes

    value = ctypes.c_uint(preference)
    ctypes.windll.dwmapi.DwmSetWindowAttribute(
        wintypes.HWND(hwnd),
        _DWMWA_WINDOW_CORNER_PREFERENCE,
        ctypes.byref(value),
        ctypes.sizeof(value),
    )
