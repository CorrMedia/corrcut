"""Subtitle overlay layout."""

from __future__ import annotations

from types import SimpleNamespace

from PySide6.QtCore import QEvent, QPoint, QPointF, QRect, QRectF, QSize, Qt
from PySide6.QtGui import QMouseEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QWidget

from corrcut.domain.subtitle_cue import SubtitleCue
from corrcut.domain.time_point import TimePoint
from corrcut.ui.preview_panel import PreviewPanel
from corrcut.ui.subtitle_overlay import SubtitleOverlay
from corrcut.ui.video_region_overlay import VideoRegionOverlay

_app = QApplication.instance() or QApplication([])


def test_subtitle_overlay_keeps_windowed_font_size() -> None:
    host = QWidget()
    host.resize(640, 320)
    overlay = SubtitleOverlay(host)

    overlay.show_cue(cue_id="cue-1", text="Hello", visible=True, editable=False)
    overlay.reposition(host, layout_reference_height=320)

    assert "font-size: 18px" in overlay._active_label().styleSheet()
    overlay.close()


def test_subtitle_overlay_grows_sublinearly_for_fullscreen() -> None:
    host = QWidget()
    host.resize(1280, 960)
    host.show()
    overlay = SubtitleOverlay(host)

    overlay.show_cue(cue_id="cue-1", text="Hello", visible=True, editable=False)
    overlay.reposition(host, layout_reference_height=320)

    assert "font-size: 26px" in overlay._active_label().styleSheet()
    overlay.close()
    host.close()


def test_subtitle_overlay_double_click_starts_edit() -> None:
    host = QWidget()
    host.resize(640, 360)
    overlay = SubtitleOverlay(host)
    overlay.show_cue(cue_id="cue-1", text="Hello", visible=True, editable=True)
    started: list[str] = []
    overlay.edit_started.connect(lambda: started.append("start"))

    QTest.mouseDClick(overlay, Qt.MouseButton.LeftButton)

    assert started == ["start"]
    assert overlay.is_editing
    overlay.close()


def test_cover_overlay_double_click_on_caption_starts_edit() -> None:
    host = QWidget()
    host.resize(800, 450)
    host.show()
    subtitle = SubtitleOverlay(host)
    subtitle.show_cue(cue_id="cue-1", text="Hello", visible=True, editable=True)
    cover = VideoRegionOverlay(host)
    cover.set_region({"x": 0.0, "y": 0.0, "width": 1.0, "height": 1.0})
    cover.layout_on_host(host)
    cover.set_caption_hit_rect(subtitle.global_hit_rect())
    cover.double_clicked.connect(subtitle.try_begin_edit_at_global)

    global_pos = subtitle.global_hit_rect().center()
    local = QPointF(cover.mapFromGlobal(global_pos))
    event = QMouseEvent(
        QEvent.Type.MouseButtonDblClick,
        local,
        QPointF(global_pos),
        Qt.MouseButton.LeftButton,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
    )
    cover.mouseDoubleClickEvent(event)

    assert subtitle.is_editing
    cover.close()
    subtitle.close()
    host.close()


def test_cover_overlay_excludes_caption_from_mask() -> None:
    host = QWidget()
    host.resize(800, 450)
    host.show()
    cover = VideoRegionOverlay(host)
    cover.set_region({"x": 0.0, "y": 0.5, "width": 1.0, "height": 0.5})
    cover.layout_on_host(host)
    caption = QRect(host.mapToGlobal(QPoint(300, 380)), QSize(200, 40))
    cover.set_caption_hit_rect(caption)
    local = cover.mapFromGlobal(caption.center())

    assert not cover.mask().contains(local)
    press = QMouseEvent(
        QEvent.Type.MouseButtonPress,
        QPointF(local),
        QPointF(caption.center()),
        Qt.MouseButton.LeftButton,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
    )
    cover.mousePressEvent(press)
    assert cover._drag_mode is None
    cover.close()
    host.close()


def _assert_caption_inside_picture(panel: PreviewPanel) -> None:
    overlay = panel._subtitle_overlay
    host = panel._video_host
    assert overlay.isVisible()
    top_left = host.mapFromGlobal(overlay.mapToGlobal(QPoint(0, 0)))
    picture_top = 90
    picture_bottom = host.height() - 90
    assert top_left.y() >= picture_top - 1
    assert top_left.y() + overlay.height() <= picture_bottom + 2


def test_resize_keeps_the_caption_on_the_video_frame() -> None:
    """Growing and shrinking the window keeps the caption inside the picture."""
    panel = PreviewPanel()
    panel.resize(900, 420)
    panel.show()
    _app.processEvents()
    cue = SubtitleCue.create(
        start=TimePoint.from_seconds_float(0.0, 48000),
        end=TimePoint.from_seconds_float(2.0, 48000),
        text="Hello from the picture",
    )
    panel._project = SimpleNamespace(subtitle_cues=[cue])
    panel._video_letterbox_rect = (  # type: ignore[method-assign]
        lambda host: QRectF(0.0, 90.0, float(host.width()), float(host.height()) - 180.0)
    )
    panel.sync_subtitle_overlay(seconds=1.0)
    panel.resize(900, 720)
    _app.processEvents()
    _assert_caption_inside_picture(panel)

    # A shorter host used to leave the caption at its old screen position,
    # which sits on the transport controls under the video.
    panel.resize(900, 420)
    _app.processEvents()
    _assert_caption_inside_picture(panel)
    overlay = panel._subtitle_overlay
    overlay.close()
    panel.close()


def test_moving_the_window_keeps_caption_and_blur_box() -> None:
    shell = QWidget()
    shell.resize(1000, 700)
    panel = PreviewPanel(shell)
    panel.setGeometry(40, 50, 800, 480)
    shell.show()
    _app.processEvents()
    cue = SubtitleCue.create(
        start=TimePoint.from_seconds_float(0.0, 48000),
        end=TimePoint.from_seconds_float(2.0, 48000),
        text="Hello from the picture",
    )
    panel._project = SimpleNamespace(subtitle_cues=[cue])
    panel.sync_subtitle_overlay(seconds=1.0)
    panel._video_region_overlay.set_region({"x": 0.2, "y": 0.2, "width": 0.3, "height": 0.3})
    panel.reposition_video_region_overlay()
    _app.processEvents()

    host = panel._video_host
    caption = panel._subtitle_overlay
    box = panel._video_region_overlay
    assert caption.isVisible()
    assert box.isVisible()
    host_before = host.mapToGlobal(QPoint(0, 0))
    caption_before = caption.mapToGlobal(QPoint(0, 0))
    box_before = box.mapToGlobal(QPoint(0, 0))

    shell.move(shell.x() + 120, shell.y() + 80)
    _app.processEvents()

    host_delta = host.mapToGlobal(QPoint(0, 0)) - host_before
    assert host_delta.manhattanLength() > 0
    assert caption.mapToGlobal(QPoint(0, 0)) - caption_before == host_delta
    assert box.mapToGlobal(QPoint(0, 0)) - box_before == host_delta
    caption.close()
    box.close()
    panel.close()
    shell.close()


def test_subtitle_checkbox_enables_after_cues_arrive() -> None:
    """Opening a file disables the box before cues exist; Whisper fills them later."""
    panel = PreviewPanel()
    panel._project = SimpleNamespace(subtitle_cues=[])
    panel.sync_subtitle_overlay(seconds=0.0)
    assert not panel._subs_toggle.isEnabled()

    cue = SubtitleCue.create(
        start=TimePoint.from_seconds_float(0.0, 48000),
        end=TimePoint.from_seconds_float(2.0, 48000),
        text="Hello",
    )
    panel._project = SimpleNamespace(subtitle_cues=[cue])
    panel.sync_subtitle_overlay(seconds=1.0)
    assert panel._subs_toggle.isEnabled()
    panel.close()


def test_pause_restores_on_screen_subtitle_editing() -> None:
    panel = PreviewPanel()
    cue = SubtitleCue.create(
        start=TimePoint.from_seconds_float(0.0, 48000),
        end=TimePoint.from_seconds_float(4.0, 48000),
        text="Hello",
    )
    panel._project = SimpleNamespace(subtitle_cues=[cue])
    panel._is_playing = True
    panel._update_subtitle_overlay_at(1.0)
    assert panel._subtitle_overlay._editable is False

    panel._is_playing = False
    panel._update_subtitle_overlay_at(1.0)

    assert panel._subtitle_overlay._editable is True
    QTest.mouseDClick(panel._subtitle_overlay, Qt.MouseButton.LeftButton)
    assert panel._subtitle_overlay.is_editing
    panel._subtitle_overlay.close()
    panel.close()


def test_cleared_region_overlay_lets_caption_clicks_through() -> None:
    host = QWidget()
    host.resize(800, 450)
    host.show()
    subtitle = SubtitleOverlay(host)
    subtitle.show_cue(cue_id="cue-1", text="Hello", visible=True, editable=True)
    cover = VideoRegionOverlay(host)
    cover.set_region({"x": 0.0, "y": 0.6, "width": 1.0, "height": 0.4})
    cover.layout_on_host(host)
    cover.set_caption_hit_rect(subtitle.global_hit_rect())
    cover.clear()

    assert cover.windowFlags() & Qt.WindowType.WindowTransparentForInput
    assert cover._ghost_layer.windowFlags() & Qt.WindowType.WindowTransparentForInput
    QTest.mouseDClick(subtitle, Qt.MouseButton.LeftButton)
    assert subtitle.is_editing

    cover.set_region({"x": 0.2, "y": 0.2, "width": 0.4, "height": 0.4})
    cover.layout_on_host(host)
    assert not cover.windowFlags() & Qt.WindowType.WindowTransparentForInput
    assert cover.isVisible()
    cover.close()
    subtitle.close()
    host.close()
