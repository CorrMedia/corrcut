"""Default side-dock tabs, and a saved layout overriding that default."""

from __future__ import annotations

from PySide6.QtCore import QSettings, Qt
from PySide6.QtWidgets import QApplication, QDockWidget, QTabBar

from corrcut.config import AppPaths
from corrcut.ui.editor_window import EditorWindow

_app = QApplication.instance() or QApplication([])


def _isolated_settings(path) -> QSettings:  # type: ignore[no-untyped-def]
    settings = QSettings(str(path), QSettings.Format.IniFormat)
    settings.clear()
    settings.sync()
    return settings


def _shutdown(window: EditorWindow) -> None:
    window._waveform_job_runner.shutdown()
    window._ai_job_runner.shutdown()
    window._filmstrip_job_runner.shutdown()
    window._scene_cut_job_runner.shutdown()
    window._job_runner.shutdown()
    window._media_open_job_runner.shutdown()


def _open(monkeypatch, settings: QSettings) -> EditorWindow:  # type: ignore[no-untyped-def]
    monkeypatch.setattr(
        "corrcut.preview.mpv_backend.is_mpv_available",
        lambda: False,
    )
    monkeypatch.setattr(EditorWindow, "_window_settings", lambda self: settings)
    window = EditorWindow(AppPaths.default())
    window.show()
    _app.processEvents()
    return window


def test_default_side_docks_tab_together_with_operations_selected(tmp_path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    settings = _isolated_settings(tmp_path / "layout.ini")
    window = _open(monkeypatch, settings)
    try:
        side = window._side_docks()
        for dock in side:
            others = set(window.tabifiedDockWidgets(dock))
            assert others == set(side) - {dock}
            assert window.dockWidgetArea(dock) == Qt.DockWidgetArea.RightDockWidgetArea
        bars = [
            bar
            for bar in window.findChildren(QTabBar)
            if bar.isVisible() and bar.count() == len(side)
        ]
        assert len(bars) == 1
        bar = bars[0]
        assert [bar.tabText(index) for index in range(bar.count())] == [
            "Operations",
            "Streams",
            "AI Suggestions",
            "Subtitles",
        ]
        assert bar.tabText(bar.currentIndex()) == "Operations"
        assert window._timeline_dock.isVisible()
        assert window._timeline_dock not in window.tabifiedDockWidgets(window._ops_dock)
    finally:
        _shutdown(window)


def test_saved_dock_layout_overrides_the_tab_default(tmp_path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    settings = _isolated_settings(tmp_path / "layout.ini")
    first = _open(monkeypatch, settings)
    try:
        first.addDockWidget(Qt.DockWidgetArea.LeftDockWidgetArea, first._track_dock)
        first._track_dock.show()
        first._save_window_layout()
        settings.sync()
    finally:
        _shutdown(first)

    second = _open(monkeypatch, settings)
    try:
        assert second.dockWidgetArea(second._track_dock) == Qt.DockWidgetArea.LeftDockWidgetArea
        assert second._track_dock not in second.tabifiedDockWidgets(second._ops_dock)
        assert isinstance(second._ops_dock, QDockWidget)
    finally:
        _shutdown(second)
