from __future__ import annotations

from corrcut.ui.preview_panel import PreviewPanel


def test_zoom_preview_stays_on_when_paused_without_a_box(qtbot) -> None:
    panel = PreviewPanel()
    qtbot.addWidget(panel)
    assert panel._apply_zoom_preview is True
    # Real play sets this; frame-step unpause does not and must not punch in.
    panel._skip_jumps_enabled = True
    panel._on_playback_state(True)
    assert panel._apply_zoom_preview is True
    panel._on_playback_state(False)
    assert panel._apply_zoom_preview is True


def test_zoom_preview_drops_only_while_a_box_is_selected(qtbot) -> None:
    panel = PreviewPanel()
    qtbot.addWidget(panel)
    panel.set_video_region({"x": 0.2, "y": 0.2, "width": 0.4, "height": 0.4})
    assert panel._apply_zoom_preview is False
    panel._skip_jumps_enabled = True
    panel._on_playback_state(True)
    assert panel._apply_zoom_preview is True
    panel._on_playback_state(False)
    assert panel._apply_zoom_preview is False
    panel.clear_video_region()
    assert panel._apply_zoom_preview is True
