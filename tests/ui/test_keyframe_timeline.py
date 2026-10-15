"""Inspector-style box keyframe track."""

from __future__ import annotations

from PySide6.QtCore import QPoint, Qt
from PySide6.QtWidgets import QApplication

from corrcut.ui.keyframe_timeline import (
    KeyframeTimeline,
    format_track_time,
    tick_seconds,
    view_span,
)

_app = QApplication.instance() or QApplication([])


def test_set_keys_enables_delete_for_animation() -> None:
    track = KeyframeTimeline()
    track.set_span(2.0, 8.0)
    track.set_keys([2.0], 0)
    assert not track.isHidden()
    assert track.key_times == [2.0]
    assert not track.delete_enabled
    track.set_keys([2.0, 5.0, 8.0], 1)
    assert track.delete_enabled
    assert track.active_index == 1
    track.clear()
    assert not track.isVisible()
    track.close()


def _press(inner, pos: QPoint, *, alt: bool = False):
    from PySide6.QtCore import QEvent, QPointF
    from PySide6.QtGui import QMouseEvent

    mods = Qt.KeyboardModifier.AltModifier if alt else Qt.KeyboardModifier.NoModifier
    return QMouseEvent(
        QEvent.Type.MouseButtonPress,
        QPointF(pos),
        QPointF(inner.mapToGlobal(pos)),
        Qt.MouseButton.LeftButton,
        Qt.MouseButton.LeftButton,
        mods,
    )


def _move(inner, pos: QPoint):
    from PySide6.QtCore import QEvent, QPointF
    from PySide6.QtGui import QMouseEvent

    return QMouseEvent(
        QEvent.Type.MouseMove,
        QPointF(pos),
        QPointF(inner.mapToGlobal(pos)),
        Qt.MouseButton.NoButton,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
    )


def _release(inner, pos: QPoint):
    from PySide6.QtCore import QEvent, QPointF
    from PySide6.QtGui import QMouseEvent

    return QMouseEvent(
        QEvent.Type.MouseButtonRelease,
        QPointF(pos),
        QPointF(inner.mapToGlobal(pos)),
        Qt.MouseButton.LeftButton,
        Qt.MouseButton.NoButton,
        Qt.KeyboardModifier.NoModifier,
    )


def test_empty_click_seeks_without_add() -> None:
    track = KeyframeTimeline()
    track.set_span(0.0, 10.0)
    track.set_keys([0.0], 0)
    track.resize(400, 52)
    track.show()
    seeks: list[float] = []
    track.seek_requested.connect(seeks.append)
    inner = track._track
    inner.resize(320, 46)
    pos = QPoint(int(inner.width() * 0.6), inner.height() // 2)
    inner.mousePressEvent(_press(inner, pos))
    assert seeks
    assert abs(seeks[0] - 6.0) < 1.0
    track.close()


def test_scrub_drag_finishes_on_release() -> None:
    track = KeyframeTimeline()
    track.set_span(0.0, 10.0)
    track.set_keys([0.0], 0)
    track.resize(400, 52)
    track.show()
    live: list[float] = []
    finished: list[float] = []
    track.seek_requested.connect(live.append)
    track.seek_finished.connect(finished.append)
    inner = track._track
    inner.resize(320, 46)
    start = QPoint(int(inner.width() * 0.2), inner.height() // 2)
    end = QPoint(int(inner.width() * 0.7), inner.height() // 2)
    inner.mousePressEvent(_press(inner, start))
    inner.mouseMoveEvent(_move(inner, end))
    inner.mouseReleaseEvent(_release(inner, end))
    assert len(live) == 2
    assert finished
    assert abs(finished[-1] - 7.0) < 1.0
    track.close()


def test_diamond_click_does_not_retime() -> None:
    track = KeyframeTimeline()
    track.set_span(0.0, 10.0)
    track.set_keys([0.0, 5.0], 0)
    track.resize(400, 52)
    track.show()
    moved: list[tuple[int, float]] = []
    track.key_moved.connect(lambda index, seconds: moved.append((index, seconds)))
    inner = track._track
    inner.resize(320, 46)
    x = inner._seconds_to_x(0.0)
    pos = QPoint(int(x), inner.height() // 2)
    inner.mousePressEvent(_press(inner, pos))
    from PySide6.QtCore import QEvent, QPointF
    from PySide6.QtGui import QMouseEvent

    release = QMouseEvent(
        QEvent.Type.MouseButtonRelease,
        QPointF(pos),
        QPointF(inner.mapToGlobal(pos)),
        Qt.MouseButton.LeftButton,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
    )
    inner.mouseReleaseEvent(release)
    assert moved == []
    assert track.active_index == 0
    track.close()


def test_tick_seconds_includes_span_ends() -> None:
    ticks = tick_seconds(10.0, 17.0)
    assert ticks[0] == 10.0
    assert ticks[-1] == 17.0
    assert any(abs(value - 15.0) < 1e-6 or abs(value - 12.0) < 1e-6 for value in ticks)


def test_view_span_includes_outside_keys() -> None:
    start, end = view_span(10.0, 17.0, [8.0, 19.0])
    assert start < 8.0
    assert end > 19.0
    assert start >= 0.0


def test_enable_window_fits_outside_keys() -> None:
    track = KeyframeTimeline()
    track.set_enable_window(10.0, 17.0)
    track.set_keys([8.0, 13.0, 19.0], 0)
    view_start, view_end = track.view_range
    assert view_start < 8.0 or view_start == 0.0
    assert view_end > 19.0
    assert track.enable_window == (10.0, 17.0)
    track.close()


def test_box_track_scrub_coalesces_then_exact_seeks() -> None:
    from PySide6.QtTest import QTest

    from corrcut.ui.preview_panel import PreviewPanel

    class _SeekLog:
        def __init__(self) -> None:
            self.calls: list[tuple[float, str]] = []

        def seek(self, seconds: float, reference: str = "absolute") -> None:
            self.calls.append((float(seconds), reference))

        def is_playing(self) -> bool:
            return False

        def video_letterbox_rect(self, *_args: object, **_kwargs: object) -> None:
            return None

    panel = PreviewPanel()
    panel._backend = _SeekLog()  # type: ignore[attr-defined]
    panel.queue_keyframe_scrub_seek(1.0)
    panel.queue_keyframe_scrub_seek(1.2)
    panel.queue_keyframe_scrub_seek(1.4)
    assert panel._backend.calls == [(1.0, "absolute+keyframes")]  # type: ignore[attr-defined]
    while panel._keyframe_scrub_timer.isActive():  # type: ignore[attr-defined]
        QTest.qWait(10)
    assert panel._backend.calls == [  # type: ignore[attr-defined]
        (1.0, "absolute+keyframes"),
        (1.4, "absolute+keyframes"),
    ]
    panel.finish_keyframe_scrub_seek(1.5)
    assert panel._backend.calls[-1] == (1.5, "absolute+exact")  # type: ignore[attr-defined]
    assert panel._exact_seek_timer.isActive() is False  # type: ignore[attr-defined]
    panel.close()


def test_format_track_time() -> None:
    assert format_track_time(3.0) == "3s"
    assert format_track_time(3.5) == "3.5s"
    assert format_track_time(65.0) == "1:05"


def test_empty_click_clears_key_selection() -> None:
    track = KeyframeTimeline()
    track.set_span(0.0, 10.0)
    track.set_keys([0.0, 5.0], 0, selected=1)
    track.resize(400, 52)
    track.show()
    cleared: list[int] = []
    track.background_pressed.connect(lambda: cleared.append(1))
    inner = track._track
    inner.resize(320, 46)
    pos = QPoint(int(inner.width() * 0.8), inner.height() // 2)
    inner.mousePressEvent(_press(inner, pos))
    assert cleared == [1]
    assert track.selected_index == -1
    track.close()


def test_diamond_click_selects_without_add() -> None:
    track = KeyframeTimeline()
    track.set_span(0.0, 10.0)
    track.set_keys([0.0, 5.0], 0)
    track.resize(400, 44)
    track.show()
    added: list[float] = []
    picked: list[int] = []
    track.add_requested.connect(added.append)
    track.index_selected.connect(picked.append)
    inner = track._track
    inner.resize(320, 36)
    x = inner._seconds_to_x(5.0)
    pos = QPoint(int(x), inner.height() // 2)
    from PySide6.QtCore import QEvent, QPointF
    from PySide6.QtGui import QMouseEvent

    press = QMouseEvent(
        QEvent.Type.MouseButtonPress,
        QPointF(pos),
        QPointF(inner.mapToGlobal(pos)),
        Qt.MouseButton.LeftButton,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
    )
    inner.mousePressEvent(press)
    assert added == []
    assert picked == [1]
    assert track.active_index == 1
    assert track.selected_index == 1
    track.close()
