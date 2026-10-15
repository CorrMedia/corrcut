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
    from pathlib import Path

    from corrcut.domain.dialogue_target import DialogueTargetStrategy
    from corrcut.domain.project import Project, SourceFileRecord, TrackRecord

    monkeypatch.setattr(
        "corrcut.preview.mpv_backend.is_mpv_available",
        lambda: False,
    )
    editor_window = EditorWindow(AppPaths.default())
    # Zoom tools stay disabled until a project with media is loaded.
    editor_window._set_project(
        Project(
            source=SourceFileRecord(
                path=Path("missing_zoom_fixture.mkv"),
                fingerprint={"test": True},
                probe={"duration_seconds": 60.0, "subtitle_streams": []},
            ),
            tracks=[
                TrackRecord(
                    stream_index=1,
                    codec="aac",
                    channel_layout="stereo",
                    channels=2,
                    sample_rate=48000,
                    language="eng",
                    dialogue_target_strategy=DialogueTargetStrategy.FULL_MIX,
                ),
            ],
            selected_audio_stream_index=1,
        )
    )
    panel = editor_window._timeline_panel
    initial = _initial_pps(panel)

    shortcuts = {
        shortcut.key().toString(): shortcut for shortcut in editor_window.findChildren(QShortcut)
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
