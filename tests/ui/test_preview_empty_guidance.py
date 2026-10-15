from __future__ import annotations

from pathlib import Path

from corrcut.preview.mpv_backend import MpvUnavailableWidget
from corrcut.ui.preview_panel import PreviewPanel


def test_empty_preview_asks_to_open_a_movie(qtbot) -> None:
    panel = PreviewPanel()
    qtbot.addWidget(panel)
    panel.resize(640, 420)
    panel.show()
    qtbot.waitExposed(panel)
    if isinstance(panel._mpv_widget, MpvUnavailableWidget):
        assert panel._empty_preview.isHidden()
    else:
        assert panel._empty_hint.text() == "Open a movie"
        assert panel._empty_preview.drop_hint.text() == "or drop a media or project file"
        assert panel._empty_preview.open_button.text() == "Open media"
        assert panel._empty_preview.open_project_button.text() == "Open project"
        assert not panel._empty_preview.isHidden()
        assert panel._empty_preview.width() > 200
        assert panel._empty_preview.height() > 120
        assert panel._mpv_widget.isHidden()
    panel._media_path = Path("clip.mp4")
    panel._sync_empty_hint()
    assert panel._empty_preview.isHidden()


def test_edited_toggle_reads_original_when_off(qtbot) -> None:
    panel = PreviewPanel()
    qtbot.addWidget(panel)
    assert panel._ab_toggle.text() == "Edited"
    assert panel._original_mark.isHidden()

    panel._ab_toggle.setChecked(False)
    assert panel._ab_toggle.text() == "Original"
    assert panel._original_mark.isHidden()

    panel._media_path = Path("clip.mp4")
    panel._sync_original_mark()
    assert not panel._original_mark.isHidden()
    assert panel._original_mark.text() == "Original"

    panel.set_edits_enabled(True)
    assert panel._ab_toggle.text() == "Edited"
    assert panel._original_mark.isHidden()


def test_transport_cluster_sits_on_the_preview_midline(qtbot) -> None:
    panel = PreviewPanel()
    qtbot.addWidget(panel)
    panel.resize(1200, 420)
    panel.show()
    qtbot.waitExposed(panel)

    def cluster_offset() -> float:
        panel._controls_bar.reposition()
        bar = panel._controls_bar
        cluster = panel._play_btn.parentWidget()
        assert cluster is not None
        center = cluster.mapTo(bar, cluster.rect().center())
        return center.x() - bar.width() / 2

    assert abs(cluster_offset()) <= 2
    bar = panel._controls_bar
    play_x = panel._play_btn.mapTo(bar, panel._play_btn.rect().center()).x()
    edited_x = panel._ab_toggle.mapTo(bar, panel._ab_toggle.rect().center()).x()
    volume_x = panel._volume_btn.mapTo(bar, panel._volume_btn.rect().center()).x()
    assert edited_x < play_x
    assert panel._subs_toggle.mapTo(bar, panel._subs_toggle.rect().center()).x() < play_x
    assert volume_x > play_x

    panel._ab_toggle.setChecked(False)
    assert panel._ab_toggle.text() == "Original"
    assert abs(cluster_offset()) <= 2
