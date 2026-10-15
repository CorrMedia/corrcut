"""Timeline scroll position survives widget resize and follows edge scrubs."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from PySide6.QtCore import QEvent, QObject, QPointF, Qt
from PySide6.QtGui import QImage, QMouseEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from corrcut.ui.timeline.timeline_lanes import SCENE_HEIGHT
from corrcut.ui.timeline.timeline_navigator import MIN_HANDLE_WIDTH
from corrcut.ui.timeline.timeline_widget import TimelineWidget, drag_autoscroll_delta

_app = QApplication.instance() or QApplication([])


def _mouse(
    event_type: QEvent.Type,
    pos: QPointF,
    *,
    button: Qt.MouseButton = Qt.MouseButton.LeftButton,
    buttons: Qt.MouseButton = Qt.MouseButton.LeftButton,
    modifiers: Qt.KeyboardModifier = Qt.KeyboardModifier.NoModifier,
) -> QMouseEvent:
    return QMouseEvent(
        event_type,
        pos,
        pos,
        QPointF(),
        button,
        buttons,
        modifiers,
    )


def _shown_scrolled_timeline() -> TimelineWidget:
    timeline = TimelineWidget()
    timeline.resize(400, int(SCENE_HEIGHT) + 40)
    timeline.show()
    timeline.set_duration(120.0)
    timeline.set_pixels_per_second(80.0)
    _app.processEvents()
    return timeline


def test_resize_preserves_horizontal_scroll() -> None:
    timeline = TimelineWidget()
    timeline.resize(400, 120)
    timeline.show()
    timeline.set_duration(120.0)
    timeline.set_pixels_per_second(80.0)

    bar = timeline.horizontalScrollBar()
    bar.setValue(2400)
    _app.processEvents()
    assert bar.value() == 2400

    timeline.resize(400, 200)
    _app.processEvents()
    assert bar.value() == 2400

    timeline.resize(320, 200)
    _app.processEvents()
    assert bar.value() > 0

    # Height-only resize should not reset scroll (cheap path, no waveform work).
    bar.setValue(1800)
    _app.processEvents()
    timeline.resize(320, 280)
    _app.processEvents()
    assert bar.value() == 1800


class _ResizePeaks:
    def __init__(self) -> None:
        self._peaks = [(0.0, 0.8), (-0.4, 0.6), (-0.2, 0.9), (0.1, 0.5)] * 8
        self.index = SimpleNamespace(levels=1)

    def level_peaks_per_second(self, level: int) -> int:
        del level
        return 200

    def has_displayable_peaks(self) -> bool:
        return True

    def peaks_for_level_if_ready(self, level: int) -> list[tuple[float, float]]:
        del level
        return self._peaks


def _rightmost_waveform_x(image: QImage) -> int | None:
    for x in range(image.width() - 1, -1, -1):
        for y in range(image.height()):
            color = image.pixelColor(x, y)
            if color.alpha() > 40 and color.green() > color.red() and color.green() > color.blue():
                return x
    return None


def test_resize_keeps_and_redraws_the_waveform() -> None:
    """A window resize used to paint a solid stand-in and then never redraw the peaks."""
    timeline = TimelineWidget()
    timeline.resize(400, int(SCENE_HEIGHT) + 40)
    timeline.show()
    QTest.qWaitForWindowExposed(timeline)
    timeline.set_peak_store(_ResizePeaks(), 30.0)  # type: ignore[arg-type]
    timeline.set_pixels_per_second(40.0)
    _app.processEvents()
    # Force a paint through the scene (viewport.grab alone can miss the item).
    timeline._waveform.update()  # noqa: SLF001
    timeline.viewport().repaint()
    _app.processEvents()
    if timeline._waveform._envelope_image.isNull():  # noqa: SLF001
        from PySide6.QtGui import QPainter
        from PySide6.QtWidgets import QStyleOptionGraphicsItem

        option = QStyleOptionGraphicsItem()
        option.exposedRect = timeline._waveform.boundingRect()  # noqa: SLF001
        painter = QPainter(timeline.viewport())
        timeline._waveform.paint(painter, option, timeline.viewport())  # noqa: SLF001
        painter.end()
    assert not timeline._waveform._envelope_image.isNull()  # noqa: SLF001

    redraws_after_preview: list[bool] = []

    class _PaintFilter(QObject):
        def eventFilter(self, watched: QObject, event: QEvent) -> bool:  # noqa: ARG002
            if (
                event.type() == QEvent.Type.Paint and not timeline._waveform._fast_resize_preview  # noqa: SLF001
            ):
                redraws_after_preview.append(True)
            return False

    watch = _PaintFilter()
    timeline.viewport().installEventFilter(watch)
    timeline.resize(720, int(SCENE_HEIGHT) + 40)
    # Windows invalidates the widget while updates are frozen. That paint must
    # keep the ribbon, not replace it with the track color.
    during = timeline.viewport().grab().toImage()
    assert timeline._waveform._fast_resize_preview is True  # noqa: SLF001
    assert _rightmost_waveform_x(during) is not None

    QTest.qWait(50)
    timeline.viewport().removeEventFilter(watch)

    assert timeline._resize_in_progress is False  # noqa: SLF001
    assert timeline._waveform._fast_resize_preview is False  # noqa: SLF001
    assert redraws_after_preview
    assert timeline._waveform.boundingRect().width() == pytest.approx(timeline.viewport().width())  # noqa: SLF001

    settled = timeline.viewport().grab().toImage()
    right = _rightmost_waveform_x(settled)
    assert right is not None
    assert right > int(settled.width() * 0.8)


def test_go_to_start_resets_playhead_and_scroll() -> None:
    timeline = TimelineWidget()
    timeline.resize(400, 120)
    timeline.show()
    timeline.set_duration(120.0)
    timeline.set_pixels_per_second(80.0)
    timeline.set_playhead(45.0)
    timeline.horizontalScrollBar().setValue(2400)
    _app.processEvents()

    timeline.go_to_start()

    assert timeline.playhead_seconds() == 0.0
    assert timeline.horizontalScrollBar().value() == 0


def test_zoom_out_does_not_open_a_gap_before_time_zero() -> None:
    """Anchoring zoom on the playhead must not scroll before the media start.

    A negative scroll draws the lanes inset, then the scrollbar clamps scroll to 0
    without redrawing. Clicks in that inset then report a real master timecode.
    """
    timeline = TimelineWidget()
    timeline.resize(400, int(SCENE_HEIGHT) + 40)
    timeline.show()
    timeline.set_duration(20.0)
    timeline.set_pixels_per_second(80.0)
    timeline.set_playhead(10.0)
    vw = float(timeline.viewport().width())
    timeline.horizontalScrollBar().setValue(int(10.0 * 80.0 - vw / 2.0))
    _app.processEvents()
    anchor = timeline.playhead_viewport_x()
    assert anchor > 50.0

    # Whole clip fits in the view; keeping the playhead anchored would scroll before 0.
    timeline.set_pixels_per_second(4.0, anchor_view_x=anchor)
    _app.processEvents()

    assert timeline._scroll_x == pytest.approx(0.0)
    assert timeline._ruler._scroll_x == pytest.approx(0.0)
    assert timeline._waveform._scroll_x == pytest.approx(0.0)
    assert timeline._filmstrip._scroll_x == pytest.approx(0.0)
    assert timeline._time_to_scene_x(0.0) == pytest.approx(0.0)
    assert timeline.playhead_viewport_x() == pytest.approx(10.0 * timeline.pixels_per_second())

    # A click left of where the inset 00:00:00 used to sit is the start of the media.
    y = float(timeline.viewport().height()) * 0.45
    press = QPointF(12.0, y)
    timeline.mousePressEvent(_mouse(QEvent.Type.MouseButtonPress, press))
    timeline.mouseReleaseEvent(
        _mouse(
            QEvent.Type.MouseButtonRelease,
            press,
            button=Qt.MouseButton.LeftButton,
            buttons=Qt.MouseButton.NoButton,
        )
    )
    assert timeline.playhead_seconds() == pytest.approx(
        12.0 / timeline.pixels_per_second(), abs=0.05
    )
    assert timeline.playhead_viewport_x() == pytest.approx(12.0, abs=1.0)


def test_keep_visible_at_start_pins_scroll_to_zero() -> None:
    timeline = TimelineWidget()
    timeline.resize(400, 120)
    timeline.show()
    timeline.set_duration(120.0)
    timeline.set_pixels_per_second(80.0)
    timeline.go_to_start()
    timeline.horizontalScrollBar().setValue(1600)
    _app.processEvents()

    timeline.scroll_playhead_keep_visible()

    assert timeline.playhead_seconds() == 0.0
    assert timeline.horizontalScrollBar().value() == 0


def test_drag_autoscroll_delta_is_zero_away_from_edges() -> None:
    assert drag_autoscroll_delta(200.0, 400.0) == 0.0


def test_drag_autoscroll_delta_pushes_outward_at_edges() -> None:
    assert drag_autoscroll_delta(400.0, 400.0) > 0
    assert drag_autoscroll_delta(0.0, 400.0) < 0


def test_scrub_past_right_edge_autoscrolls() -> None:
    timeline = _shown_scrolled_timeline()
    vw = float(timeline.viewport().width())
    press = QPointF(vw * 0.4, 8.0)
    timeline.mousePressEvent(_mouse(QEvent.Type.MouseButtonPress, press))
    start_s = timeline.playhead_seconds()
    assert timeline.horizontalScrollBar().value() == 0

    past_edge = QPointF(vw + 40.0, 8.0)
    timeline.mouseMoveEvent(
        _mouse(QEvent.Type.MouseMove, past_edge, buttons=Qt.MouseButton.LeftButton)
    )
    assert timeline.horizontalScrollBar().value() > 0
    assert timeline.playhead_seconds() > start_s
    assert 0.0 <= timeline.playhead_viewport_x() <= vw + 1.0
    assert timeline._drag_autoscroll_timer.isActive()

    timeline.mouseReleaseEvent(
        _mouse(
            QEvent.Type.MouseButtonRelease,
            past_edge,
            button=Qt.MouseButton.LeftButton,
            buttons=Qt.MouseButton.NoButton,
        )
    )
    assert not timeline._drag_autoscroll_timer.isActive()


def test_scrub_hold_at_right_edge_keeps_scrolling() -> None:
    timeline = _shown_scrolled_timeline()
    vw = float(timeline.viewport().width())
    timeline.mousePressEvent(_mouse(QEvent.Type.MouseButtonPress, QPointF(vw * 0.5, 8.0)))
    past_edge = QPointF(vw + 24.0, 8.0)
    timeline.mouseMoveEvent(
        _mouse(QEvent.Type.MouseMove, past_edge, buttons=Qt.MouseButton.LeftButton)
    )
    after_move = timeline.horizontalScrollBar().value()
    timeline._on_drag_autoscroll_tick()
    timeline._on_drag_autoscroll_tick()
    assert timeline.horizontalScrollBar().value() > after_move
    timeline.mouseReleaseEvent(
        _mouse(
            QEvent.Type.MouseButtonRelease,
            past_edge,
            button=Qt.MouseButton.LeftButton,
            buttons=Qt.MouseButton.NoButton,
        )
    )


def test_scrub_past_left_edge_autoscrolls() -> None:
    timeline = _shown_scrolled_timeline()
    timeline.horizontalScrollBar().setValue(800)
    _app.processEvents()
    start_scroll = timeline.horizontalScrollBar().value()
    assert start_scroll > 0

    vw = float(timeline.viewport().width())
    timeline.mousePressEvent(_mouse(QEvent.Type.MouseButtonPress, QPointF(vw * 0.3, 8.0)))
    timeline.mouseMoveEvent(
        _mouse(QEvent.Type.MouseMove, QPointF(-20.0, 8.0), buttons=Qt.MouseButton.LeftButton)
    )
    assert timeline.horizontalScrollBar().value() < start_scroll
    timeline.mouseReleaseEvent(
        _mouse(
            QEvent.Type.MouseButtonRelease,
            QPointF(-20.0, 8.0),
            button=Qt.MouseButton.LeftButton,
            buttons=Qt.MouseButton.NoButton,
        )
    )


def test_navigator_handle_stays_wide_when_zoomed_in() -> None:
    timeline = _shown_scrolled_timeline()
    timeline.set_pixels_per_second(400.0)
    _app.processEvents()
    handle = timeline._navigator.handle_rect()
    assert handle.width() >= MIN_HANDLE_WIDTH - 0.5
    true_frac = timeline.viewport().width() / (120.0 * 400.0)
    assert handle.width() > true_frac * timeline._navigator.width() * 10


def test_navigator_click_jumps_across_the_clip() -> None:
    timeline = _shown_scrolled_timeline()
    timeline.set_pixels_per_second(400.0)
    _app.processEvents()
    bar = timeline.horizontalScrollBar()
    assert bar.value() == 0
    assert bar.maximum() > 1000

    nav = timeline._navigator
    click = QPointF(nav.width() * 0.85, nav.height() / 2.0)
    nav.mousePressEvent(_mouse(QEvent.Type.MouseButtonPress, click))
    jumped = bar.value()
    nav.mouseReleaseEvent(
        _mouse(
            QEvent.Type.MouseButtonRelease,
            click,
            button=Qt.MouseButton.LeftButton,
            buttons=Qt.MouseButton.NoButton,
        )
    )
    assert jumped > bar.maximum() * 0.6


def test_navigator_drag_stays_fine_when_zoomed_in() -> None:
    timeline = _shown_scrolled_timeline()
    timeline.set_pixels_per_second(400.0)
    _app.processEvents()
    bar = timeline.horizontalScrollBar()
    nav = timeline._navigator
    handle = nav.handle_rect()
    press = QPointF(handle.center().x(), handle.center().y())
    nav.mousePressEvent(_mouse(QEvent.Type.MouseButtonPress, press))
    nav.mouseMoveEvent(
        _mouse(
            QEvent.Type.MouseMove,
            QPointF(press.x() + 24.0, press.y()),
            buttons=Qt.MouseButton.LeftButton,
        )
    )
    moved = bar.value()
    nav.mouseReleaseEvent(
        _mouse(
            QEvent.Type.MouseButtonRelease,
            QPointF(press.x() + 24.0, press.y()),
            button=Qt.MouseButton.LeftButton,
            buttons=Qt.MouseButton.NoButton,
        )
    )
    expected = 24.0 * float(timeline.viewport().width()) / handle.width()
    assert moved == pytest.approx(expected, rel=0.25)
    travel = max(1.0, nav.width() - handle.width())
    full_clip_jump = 24.0 * bar.maximum() / travel
    assert moved < full_clip_jump * 0.2
    assert moved < bar.maximum() * 0.02


def _release(nav, pos: QPointF) -> None:
    nav.mouseReleaseEvent(
        _mouse(
            QEvent.Type.MouseButtonRelease,
            pos,
            button=Qt.MouseButton.LeftButton,
            buttons=Qt.MouseButton.NoButton,
        )
    )


def test_navigator_shift_drag_tracks_like_a_scrollbar() -> None:
    timeline = _shown_scrolled_timeline()
    timeline.set_pixels_per_second(400.0)
    _app.processEvents()
    bar = timeline.horizontalScrollBar()
    nav = timeline._navigator
    handle = nav.handle_rect()
    press = QPointF(handle.center().x(), handle.center().y())
    shift = Qt.KeyboardModifier.ShiftModifier
    nav.mousePressEvent(_mouse(QEvent.Type.MouseButtonPress, press, modifiers=shift))
    dest = QPointF(press.x() + 24.0, press.y())
    nav.mouseMoveEvent(
        _mouse(
            QEvent.Type.MouseMove,
            dest,
            buttons=Qt.MouseButton.LeftButton,
            modifiers=shift,
        )
    )
    moved = bar.value()
    _release(nav, dest)
    travel = max(1.0, nav.width() - handle.width())
    expected = 24.0 * bar.maximum() / travel
    assert moved == pytest.approx(expected, abs=2)


def test_navigator_shift_during_drag_switches_without_jumping() -> None:
    timeline = _shown_scrolled_timeline()
    timeline.set_pixels_per_second(400.0)
    _app.processEvents()
    bar = timeline.horizontalScrollBar()
    nav = timeline._navigator
    handle = nav.handle_rect()
    press = QPointF(handle.center().x(), handle.center().y())
    shift = Qt.KeyboardModifier.ShiftModifier
    nav.mousePressEvent(_mouse(QEvent.Type.MouseButtonPress, press))
    fine_pos = QPointF(press.x() + 24.0, press.y())
    nav.mouseMoveEvent(_mouse(QEvent.Type.MouseMove, fine_pos, buttons=Qt.MouseButton.LeftButton))
    fine = bar.value()
    assert fine > 0

    nav.mouseMoveEvent(
        _mouse(
            QEvent.Type.MouseMove,
            fine_pos,
            buttons=Qt.MouseButton.LeftButton,
            modifiers=shift,
        )
    )
    assert bar.value() == fine

    direct_pos = QPointF(press.x() + 48.0, press.y())
    nav.mouseMoveEvent(
        _mouse(
            QEvent.Type.MouseMove,
            direct_pos,
            buttons=Qt.MouseButton.LeftButton,
            modifiers=shift,
        )
    )
    travel = max(1.0, nav.width() - handle.width())
    direct_step = 24.0 * bar.maximum() / travel
    assert bar.value() == pytest.approx(fine + direct_step, abs=2)
    assert bar.value() - fine > fine * 5

    nav.mouseMoveEvent(_mouse(QEvent.Type.MouseMove, direct_pos, buttons=Qt.MouseButton.LeftButton))
    held = bar.value()
    release_pos = QPointF(press.x() + 72.0, press.y())
    nav.mouseMoveEvent(
        _mouse(QEvent.Type.MouseMove, release_pos, buttons=Qt.MouseButton.LeftButton)
    )
    damped_step = 24.0 * float(timeline.viewport().width()) / handle.width()
    assert bar.value() == pytest.approx(held + damped_step, rel=0.25)
    _release(nav, release_pos)
