"""Welcome / load-project screen contrast."""

from __future__ import annotations

from pathlib import Path

import pytest
from PySide6.QtCore import QRect, QSettings, QSize
from PySide6.QtGui import QColor, QImage, QPainter, QPalette
from PySide6.QtWidgets import (
    QApplication,
    QLabel,
    QListWidgetItem,
    QPushButton,
    QStyle,
    QStyleOptionViewItem,
)

from corrcut.config import APP_NAME
from corrcut.io.recent_posters import POSTER_HEIGHT, POSTER_WIDTH, poster_cache_path
from corrcut.io.recent_projects import (
    _KEY_RECENT_PATHS,
    _SETTINGS_APP,
    _SETTINGS_ORG,
    add_recent_project,
    list_recent_projects,
)
from corrcut.ui.square_menus import install_square_menu_corners
from corrcut.ui.theme import list_hover_color
from corrcut.ui.welcome_window import WelcomeWindow

_app = QApplication.instance() or QApplication([])


@pytest.fixture
def _clear_recent_settings() -> None:
    settings = QSettings(_SETTINGS_ORG, _SETTINGS_APP)
    settings.remove(_KEY_RECENT_PATHS)
    yield
    settings.remove(_KEY_RECENT_PATHS)


def _dark_palette() -> QPalette:
    palette = QPalette()
    palette.setColor(QPalette.ColorRole.Window, QColor("#202020"))
    palette.setColor(QPalette.ColorRole.WindowText, QColor("#f2f2f2"))
    palette.setColor(QPalette.ColorRole.Mid, QColor("#333333"))
    return palette


def test_welcome_secondary_labels_avoid_mid_palette_color() -> None:
    window = WelcomeWindow()
    window.setPalette(_dark_palette())
    labels = window.findChildren(QLabel)
    texts = {label.text() for label in labels}
    assert "Open a recent project or start a new one." not in texts
    assert "Recent projects" not in texts
    assert APP_NAME in texts
    secondary = [label for label in labels if label.text() == "No recent projects yet."]
    assert secondary
    for label in secondary:
        css = label.styleSheet()
        assert "palette(mid)" not in css
        assert "color:" in css
        assert "#333333" not in css.lower()


def _close(color: QColor, expected: QColor, tolerance: int = 8) -> bool:
    return (
        abs(color.red() - expected.red()) <= tolerance
        and abs(color.green() - expected.green()) <= tolerance
        and abs(color.blue() - expected.blue()) <= tolerance
    )


def _paint_recent_row(state: QStyle.StateFlag) -> QImage:
    install_square_menu_corners(_app)
    palette = _dark_palette()
    palette.setColor(QPalette.ColorRole.Base, QColor("#1a1a1a"))
    palette.setColor(QPalette.ColorRole.Text, QColor("#f2f2f2"))
    palette.setColor(QPalette.ColorRole.Highlight, QColor("#3d6ea8"))
    palette.setColor(QPalette.ColorRole.HighlightedText, QColor("#ffffff"))
    window = WelcomeWindow()
    window.setPalette(palette)
    listing = window._recent_list
    listing.setPalette(palette)
    listing.clear()
    listing.addItem(QListWidgetItem("Demo"))
    index = listing.model().index(0, 0)
    option = QStyleOptionViewItem()
    option.rect = QRect(0, 0, 320, 48)
    option.widget = listing
    option.palette = palette
    option.font = listing.font()
    option.state = QStyle.StateFlag.State_Enabled | state
    image = QImage(option.rect.size(), QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(QColor("#ff00ff"))
    painter = QPainter(image)
    listing.itemDelegate().paint(painter, option, index)
    painter.end()
    return image


def test_remove_from_list_is_a_context_action(
    tmp_path: Path, _clear_recent_settings: None
) -> None:
    path = tmp_path / "demo.corrcut"
    path.touch()
    add_recent_project(path)
    window = WelcomeWindow()
    button_labels = [button.text() for button in window.findChildren(QPushButton)]
    assert "Open" not in button_labels
    assert "Remove from list" not in button_labels

    opened: list[Path] = []
    window.recent_project_requested.connect(opened.append)
    menu = window._recent_context_menu(path.resolve())
    assert [action.text() for action in menu.actions()] == ["Open", "Remove from list"]
    menu.actions()[0].trigger()
    assert opened == [path.resolve()]
    menu.actions()[1].trigger()
    assert list_recent_projects() == []


def test_recent_row_shows_title_tooltip_and_poster(
    tmp_path: Path, _clear_recent_settings: None
) -> None:
    project = tmp_path / "Night Watch.corrcut"
    project.touch()
    add_recent_project(project)
    cache = tmp_path / "cache"
    poster = poster_cache_path(cache, project.resolve())
    poster.parent.mkdir(parents=True)
    image = QImage(POSTER_WIDTH, POSTER_HEIGHT, QImage.Format.Format_RGB32)
    image.fill(QColor("#ff0000"))
    assert image.save(str(poster), "JPEG")

    window = WelcomeWindow(cache)
    item = window._recent_list.item(0)
    assert item is not None
    assert item.text() == "Night Watch"
    assert item.toolTip() == str(project.resolve())
    poster_image = item.icon().pixmap(QSize(POSTER_WIDTH, POSTER_HEIGHT)).toImage()
    assert poster_image.pixelColor(0, 0).alpha() == 0
    color = poster_image.pixelColor(poster_image.width() // 2, poster_image.height() // 2)
    assert color.red() > 180
    assert color.blue() < 80


def test_recent_row_uses_a_placeholder_when_the_poster_is_missing(
    tmp_path: Path, _clear_recent_settings: None
) -> None:
    missing = tmp_path / "Gone.corrcut"
    add_recent_project(missing)
    window = WelcomeWindow(tmp_path / "cache")
    item = window._recent_list.item(0)
    assert item is not None
    assert item.text() == "Gone"
    assert item.toolTip() == f"{missing.resolve()}\nFile not found"
    poster_image = item.icon().pixmap(QSize(POSTER_WIDTH, POSTER_HEIGHT)).toImage()
    assert poster_image.pixelColor(0, 0).alpha() == 0
    color = poster_image.pixelColor(poster_image.width() // 2, poster_image.height() // 2)
    assert color.lightness() < 80


def test_recent_row_hover_is_not_the_selection_highlight() -> None:
    highlight = QColor("#3d6ea8")
    hover = list_hover_color(QColor("#1a1a1a"), highlight)
    hovered = _paint_recent_row(QStyle.StateFlag.State_MouseOver)
    selected = _paint_recent_row(QStyle.StateFlag.State_Selected)
    # Interior corner is fill, clear of the label.
    assert _close(hovered.pixelColor(2, 2), hover)
    assert not _close(hovered.pixelColor(2, 2), highlight)
    assert _close(selected.pixelColor(2, 2), highlight)
    assert not _close(selected.pixelColor(2, 2), hover)
    assert _light_pixels(selected, 8, 40) > 8
    idle = _paint_recent_row(QStyle.StateFlag.State_None)
    assert abs(_text_left(idle) - _text_left(selected)) <= 2


def _text_left(image: QImage) -> int:
    for x in range(image.width()):
        for y in range(image.height()):
            color = image.pixelColor(x, y)
            if color.lightness() > 200 and abs(color.red() - color.blue()) < 48:
                return x
    return -1


def _light_pixels(image: QImage, top: int, bottom: int) -> int:
    count = 0
    for y in range(top, bottom):
        for x in range(8, image.width()):
            if image.pixelColor(x, y).lightness() > 200:
                count += 1
    return count
