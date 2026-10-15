"""Preview fullscreen reparenting."""

from __future__ import annotations

from unittest.mock import MagicMock

from PySide6.QtCore import Qt
from PySide6.QtGui import QKeyEvent
from PySide6.QtWidgets import QApplication, QWidget

from corrcut.ui.preview_panel import PreviewPanel, _PreviewFullscreenWindow, _VideoOverlayHost

_app = QApplication.instance() or QApplication([])


def _mock_preview_backend() -> MagicMock:
    backend = MagicMock()
    backend.video_letterbox_rect.return_value = None
    return backend


def test_overlay_host_lowers_content_before_raising_button() -> None:
    host = _VideoOverlayHost()
    host.resize(640, 360)
    host.show()
    content = QWidget(host)
    host.content_layout().addWidget(content)
    button = QWidget(host)
    host.set_overlay(button)

    host.raise_overlay()
    assert button.isVisible()
    assert button.parent() is host


def test_fullscreen_reparents_subtitle_overlay() -> None:
    panel = PreviewPanel()
    panel._backend = _mock_preview_backend()
    old_widget = panel._mpv_widget
    panel._video_layout_slot.removeWidget(old_widget)
    old_widget.setParent(None)
    old_widget.deleteLater()
    panel._mpv_widget = QWidget(panel._video_host)
    panel._video_layout_slot.addWidget(panel._mpv_widget)

    assert panel._subtitle_overlay.parent() is panel._video_host
    assert panel._subtitle_overlay.isWindow()

    panel.enter_fullscreen()

    assert panel.is_fullscreen
    assert panel._fullscreen_window is not None
    assert panel._subtitle_overlay.parent() is panel._fullscreen_window
    assert panel._subtitle_overlay.isWindow()

    panel.exit_fullscreen()

    assert not panel.is_fullscreen
    assert panel._subtitle_overlay.parent() is panel._video_host
    panel.close()


def test_fullscreen_reparents_video_widget() -> None:
    panel = PreviewPanel()
    panel._backend = _mock_preview_backend()
    old_widget = panel._mpv_widget
    panel._video_layout_slot.removeWidget(old_widget)
    old_widget.setParent(None)
    old_widget.deleteLater()
    panel._mpv_widget = QWidget(panel._video_host)
    panel._video_layout_slot.addWidget(panel._mpv_widget)

    panel.enter_fullscreen()

    assert panel.is_fullscreen
    assert panel._fullscreen_btn.icon().cacheKey() == panel._fullscreen_exit_icon.cacheKey()
    assert panel._fullscreen_window is not None
    assert panel._video_layout_slot.indexOf(panel._mpv_widget) == -1

    panel.exit_fullscreen()

    assert not panel.is_fullscreen
    assert panel._fullscreen_btn.icon().cacheKey() == panel._fullscreen_enter_icon.cacheKey()
    assert panel._video_layout_slot.indexOf(panel._mpv_widget) >= 0
    panel.close()


def test_enter_fullscreen_requires_mpv() -> None:
    panel = PreviewPanel()
    panel._backend = None
    panel.enter_fullscreen()
    assert not panel.is_fullscreen
    panel.close()


def test_fullscreen_emits_state_signal() -> None:
    panel = PreviewPanel()
    panel._backend = _mock_preview_backend()
    old_widget = panel._mpv_widget
    panel._video_layout_slot.removeWidget(old_widget)
    old_widget.setParent(None)
    old_widget.deleteLater()
    panel._mpv_widget = QWidget(panel._video_host)
    panel._video_layout_slot.addWidget(panel._mpv_widget)
    states: list[bool] = []
    panel.fullscreen_changed.connect(states.append)

    panel.enter_fullscreen()
    panel.exit_fullscreen()

    assert states == [True, False]
    panel.close()


def test_fullscreen_cursor_hides_after_inactivity() -> None:
    panel = PreviewPanel()
    window = _PreviewFullscreenWindow(panel)
    window.show()

    window._show_cursor()
    assert QApplication.overrideCursor() is None

    window._hide_cursor()
    assert QApplication.overrideCursor() is not None
    assert QApplication.overrideCursor().shape() == Qt.CursorShape.BlankCursor

    window.restore_cursor()
    assert QApplication.overrideCursor() is None


def test_fullscreen_pointer_activity_restores_cursor() -> None:
    panel = PreviewPanel()
    window = _PreviewFullscreenWindow(panel)
    window.show()
    window._hide_cursor()

    window._on_pointer_activity()

    assert QApplication.overrideCursor() is None
    assert window._hide_cursor_timer.isActive()

    window.restore_cursor()


def test_fullscreen_exit_button_hides_with_cursor() -> None:
    panel = PreviewPanel()
    window = _PreviewFullscreenWindow(panel)
    panel._fullscreen_window = window
    panel._is_fullscreen = True
    panel._fullscreen_btn.setParent(window)
    window.show()

    window._show_cursor()
    assert panel._fullscreen_btn.isVisible()

    window._hide_cursor()
    assert not panel._fullscreen_btn.isVisible()

    window._on_pointer_activity()
    assert panel._fullscreen_btn.isVisible()

    window.restore_cursor()
    assert panel._fullscreen_btn.isVisible()


def test_alt_enter_in_fullscreen_toggles_out() -> None:
    panel = PreviewPanel()
    panel._backend = _mock_preview_backend()
    old_widget = panel._mpv_widget
    panel._video_layout_slot.removeWidget(old_widget)
    old_widget.setParent(None)
    old_widget.deleteLater()
    panel._mpv_widget = QWidget(panel._video_host)
    panel._video_layout_slot.addWidget(panel._mpv_widget)

    panel.enter_fullscreen()
    window = panel._fullscreen_window
    assert window is not None

    window.keyPressEvent(
        QKeyEvent(
            QKeyEvent.Type.KeyPress,
            Qt.Key.Key_Return,
            Qt.KeyboardModifier.AltModifier,
        ),
    )

    assert not panel.is_fullscreen
    panel.close()
