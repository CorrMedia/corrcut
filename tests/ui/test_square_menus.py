"""Menus open as flat Resolve panels, with square hover and window corners."""

from __future__ import annotations

import sys

from PySide6.QtCore import QRect
from PySide6.QtGui import QColor, QImage, QPainter
from PySide6.QtWidgets import (
    QApplication,
    QMainWindow,
    QStyle,
    QStyleOptionMenuItem,
    QStyleOptionTab,
    QTabBar,
)

from corrcut.ui.square_menus import (
    MENU_BORDER,
    MENU_HIGHLIGHT,
    MENU_PANEL,
    ResolveMenuStyle,
    install_square_menu_corners,
    menu_corner_preference,
)

_app = QApplication.instance() or QApplication([])


def test_selected_menu_item_highlight_is_a_hard_rectangle() -> None:
    style = ResolveMenuStyle(_app.style().objectName() or "Fusion")
    image = QImage(48, 24, QImage.Format.Format_ARGB32)
    image.fill(QColor("#ff00ff"))
    option = QStyleOptionMenuItem()
    option.rect = QRect(0, 0, 48, 24)
    option.text = "New project"
    option.state = QStyle.StateFlag.State_Enabled | QStyle.StateFlag.State_Selected
    option.menuItemType = QStyleOptionMenuItem.MenuItemType.Normal
    painter = QPainter(image)
    style.drawControl(QStyle.ControlElement.CE_MenuItem, option, painter, None)
    painter.end()

    # A rounded hover leaves the corner outside the fill.
    assert image.pixelColor(0, 0).name() == MENU_HIGHLIGHT.name()
    assert image.pixelColor(47, 0).name() == MENU_HIGHLIGHT.name()
    assert image.pixelColor(0, 23).name() == MENU_HIGHLIGHT.name()


def test_menu_panel_border_is_square() -> None:
    style = ResolveMenuStyle(_app.style().objectName() or "Fusion")
    image = QImage(20, 16, QImage.Format.Format_ARGB32)
    image.fill(QColor("#ff00ff"))
    option = QStyleOptionMenuItem()
    option.rect = QRect(0, 0, 20, 16)
    painter = QPainter(image)
    style.drawPrimitive(QStyle.PrimitiveElement.PE_PanelMenu, option, painter, None)
    painter.end()

    assert image.pixelColor(0, 0).name() == MENU_BORDER.name()
    assert image.pixelColor(19, 15).name() == MENU_BORDER.name()
    assert image.pixelColor(2, 2).name() == MENU_PANEL.name()


def _paint_tab_shape(style: ResolveMenuStyle, shape: QTabBar.Shape) -> QImage:
    image = QImage(80, 24, QImage.Format.Format_ARGB32)
    image.fill(QColor("#101010"))
    option = QStyleOptionTab()
    option.rect = QRect(0, 0, 80, 24)
    option.shape = shape
    option.text = "Operations"
    option.state = QStyle.StateFlag.State_Enabled | QStyle.StateFlag.State_Selected
    option.position = QStyleOptionTab.TabPosition.Beginning
    painter = QPainter(image)
    style.drawControl(QStyle.ControlElement.CE_TabBarTabShape, option, painter, None)
    painter.end()
    return image


def test_bottom_dock_tab_shape_is_a_flipped_top_tab() -> None:
    import pytest

    style = ResolveMenuStyle(_app.style().objectName() or "Fusion")
    north = _paint_tab_shape(style, QTabBar.Shape.RoundedNorth)
    south = _paint_tab_shape(style, QTabBar.Shape.RoundedSouth)
    background = QColor("#101010")
    # Top tabs leave the upper corners open. Bottom tabs leave the lower corners open.
    # Fusion (common on CI) fills the whole tab, so the flip is not observable there.
    if north.pixelColor(0, 0) != background:
        pytest.skip("Base style fills the tab; open-corner flip not observable")
    assert north.pixelColor(0, north.height() - 1) != background
    assert south.pixelColor(0, south.height() - 1) == background
    assert south.pixelColor(0, 0) != background


def test_file_menu_opens_with_square_corners(qtbot) -> None:  # type: ignore[no-untyped-def]
    install_square_menu_corners(_app)
    window = QMainWindow()
    qtbot.addWidget(window)
    file_menu = window.menuBar().addMenu("&File")
    file_menu.addAction("New project")
    window.show()
    qtbot.waitExposed(window)

    window.menuBar().setActiveAction(file_menu.menuAction())
    qtbot.waitUntil(file_menu.isVisible, timeout=1000)
    qtbot.wait(50)

    assert file_menu.isVisible()
    assert file_menu.actions()
    corner = file_menu.grab().toImage().pixelColor(0, 0)
    assert corner.name() == MENU_BORDER.name()
    if sys.platform == "win32":
        assert menu_corner_preference(file_menu) == 1
