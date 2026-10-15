"""Toolbar icons via qtawesome (Material, Phosphor, Codicons, …)."""

from __future__ import annotations

import os
import sys
from pathlib import Path

os.environ.setdefault("QT_API", "pyside6")

import qtawesome as qta
from PySide6.QtCore import QEvent, QObject, Qt
from PySide6.QtGui import QColor, QIcon
from PySide6.QtWidgets import QApplication, QWidget

_RESOURCES = Path(__file__).resolve().parents[1] / "resources"

# Match the painted transport glyphs in transport_controls.
_ICON_COLOR = "#e8e8e8"
_ICON_MUTED = "#8a909c"


def app_icon(
    name: str,
    *,
    color: str | QColor | None = None,
    scale_factor: float = 1.0,
    muted: bool = False,
    rotated: float = 0.0,
) -> QIcon:
    """Return a themed ``QIcon`` for a qtawesome name such as ``mdi6.magnet``."""
    if color is None:
        color = _ICON_MUTED if muted else _ICON_COLOR
    elif isinstance(color, QColor):
        color = color.name()
    options: dict[str, object] = {"scale_factor": scale_factor}
    if rotated:
        options["rotated"] = float(rotated)
    return qta.icon(name, color=color, options=[options])


def corrcut_icon() -> QIcon:
    """Application mark: the CorrMedia-matched geometric C."""
    ico = _RESOURCES / "corrcut.ico"
    png = _RESOURCES / "corrcut-icon.png"
    path = ico if sys.platform == "win32" and ico.is_file() else png
    return QIcon(str(path))


def install_window_icons(app: QApplication) -> None:
    """Use the CorrCUT mark on every top-level window, including the title bar."""
    icon = corrcut_icon()
    app.setWindowIcon(icon)
    app.installEventFilter(_CaptionIconFilter(icon, app))


class _CaptionIconFilter(QObject):
    def __init__(self, icon: QIcon, parent: QObject) -> None:
        super().__init__(parent)
        self._icon = icon

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        if event.type() != QEvent.Type.Show or not isinstance(watched, QWidget):
            return False
        if not watched.isWindow() or watched.windowFlags() & Qt.WindowType.Popup:
            return False
        watched.setWindowIcon(self._icon)
        _apply_windows_caption_icon(int(watched.winId()))
        return False


def _apply_windows_caption_icon(hwnd: int) -> None:
    """Windows uses the small window icon in the title bar, not only the Qt pixmap."""
    if sys.platform != "win32" or hwnd == 0:
        return
    small, big = _windows_caption_icons()
    if small == 0 and big == 0:
        return
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.windll.user32
    send = user32.SendMessageW
    send.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
    send.restype = ctypes.c_ssize_t
    wm_seticon = 0x0080
    if small:
        send(hwnd, wm_seticon, 0, small)  # ICON_SMALL, title bar
        send(hwnd, wm_seticon, 2, small)  # ICON_SMALL2
    if big:
        send(hwnd, wm_seticon, 1, big)  # ICON_BIG


_small_caption_icon = 0
_big_caption_icon = 0


def _windows_caption_icons() -> tuple[int, int]:
    global _small_caption_icon, _big_caption_icon
    if _small_caption_icon or _big_caption_icon:
        return _small_caption_icon, _big_caption_icon
    ico = _RESOURCES / "corrcut.ico"
    if not ico.is_file():
        return 0, 0
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.windll.user32
    load = user32.LoadImageW
    load.argtypes = [
        wintypes.HINSTANCE,
        wintypes.LPCWSTR,
        wintypes.UINT,
        ctypes.c_int,
        ctypes.c_int,
        wintypes.UINT,
    ]
    load.restype = wintypes.HANDLE
    image_icon = 1
    load_from_file = 0x0010
    path = str(ico)
    small_w = user32.GetSystemMetrics(49)  # SM_CXSMICON
    small_h = user32.GetSystemMetrics(50)  # SM_CYSMICON
    big_w = user32.GetSystemMetrics(11)  # SM_CXICON
    big_h = user32.GetSystemMetrics(12)  # SM_CYICON
    _small_caption_icon = int(load(None, path, image_icon, small_w, small_h, load_from_file) or 0)
    _big_caption_icon = int(load(None, path, image_icon, big_w, big_h, load_from_file) or 0)
    return _small_caption_icon, _big_caption_icon
