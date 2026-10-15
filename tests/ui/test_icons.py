"""qtawesome toolbar icons."""

from PySide6.QtWidgets import QApplication, QWidget

from corrcut.ui.icons import app_icon, corrcut_icon, install_window_icons

_app = QApplication.instance() or QApplication([])


def test_app_icon_loads_material_magnet() -> None:
    icon = app_icon("mdi6.magnet")
    assert not icon.isNull()
    pixmap = icon.pixmap(18, 18)
    assert not pixmap.isNull()
    assert pixmap.width() > 0
    assert pixmap.height() > 0


def test_corrcut_icon_loads() -> None:
    icon = corrcut_icon()
    assert not icon.isNull()
    pixmap = icon.pixmap(32, 32)
    assert not pixmap.isNull()
    assert pixmap.width() > 0
    assert pixmap.height() > 0


def test_shown_window_uses_corrcut_icon() -> None:
    install_window_icons(_app)
    window = QWidget()
    window.show()
    _app.processEvents()
    assert not window.windowIcon().isNull()
    window.close()
