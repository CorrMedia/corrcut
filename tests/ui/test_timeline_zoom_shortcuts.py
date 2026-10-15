"""Timeline zoom via keyboard shortcuts and panel API."""

from __future__ import annotations

from PySide6.QtGui import QShortcut
from PySide6.QtWidgets import QApplication

from corrcut.config import AppPaths
from corrcut.ui.editor_window import EditorWindow
from corrcut.ui.timeline.timeline_panel import TimelinePanel

_app = QApplication.instance() or QApplication([])


def _initial_pps(panel: TimelinePanel) -> float:
    panel.timeline.resize(400, 120)
    panel.timeline.show()
    panel.timeline.set_duration(60.0)
    _app.processEvents()
    return panel.timeline.pixels_per_second()


def test_timeline_panel_zoom_methods() -> None:
    panel = TimelinePanel()
    initial = _initial_pps(panel)

    panel.zoom_in()
    zoomed_in = panel.timeline.pixels_per_second()
    assert zoomed_in > initial

    panel.zoom_out()
    assert panel.timeline.pixels_per_second() < zoomed_in


def test_ctrl_plus_minus_shortcuts_zoom_timeline(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setattr(
        "corrcut.preview.mpv_backend.is_mpv_available",
        lambda: False,
    )
    editor_window = EditorWindow(AppPaths.default())
    panel = editor_window._timeline_panel
    initial = _initial_pps(panel)

    shortcuts = {
        shortcut.key().toString(): shortcut
        for shortcut in editor_window.findChildren(QShortcut)
    }
    zoom_in = shortcuts.get("Ctrl++") or shortcuts.get("Ctrl+=")
    assert zoom_in is not None
    assert shortcuts.get("Ctrl+-") is not None

    zoom_in.activated.emit()
    zoomed_in = panel.timeline.pixels_per_second()
    assert zoomed_in > initial

    shortcuts["Ctrl+-"].activated.emit()
    assert panel.timeline.pixels_per_second() < zoomed_in

    editor_window._waveform_job_runner.shutdown()
    editor_window._ai_job_runner.shutdown()
    editor_window._job_runner.shutdown()
    editor_window._media_open_job_runner.shutdown()
