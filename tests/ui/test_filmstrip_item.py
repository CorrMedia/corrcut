"""Video filmstrip item reports missing tile times without ffmpeg."""

from PySide6.QtGui import QImage, QPainter
from PySide6.QtWidgets import QApplication

from corrcut.ui.timeline.filmstrip_item import VideoFilmstripItem

_app = QApplication.instance() or QApplication([])


def _paint(item: VideoFilmstripItem) -> None:
    image = QImage(
        int(item.boundingRect().width()),
        int(item.boundingRect().height()),
        QImage.Format.Format_RGB32,
    )
    painter = QPainter(image)
    try:
        item.paint(painter, None, None)
    finally:
        painter.end()


class _Requests:
    def __init__(self) -> None:
        self.visible: list[float] = []
        self.prefetch: list[float] = []
        self.calls = 0

    def __call__(self, visible: list[float], prefetch: list[float]) -> None:
        self.calls += 1
        self.visible.extend(visible)
        self.prefetch.extend(prefetch)

    def clear(self) -> None:
        self.visible.clear()
        self.prefetch.clear()


def test_filmstrip_requests_the_center_before_neighbors() -> None:
    item = VideoFilmstripItem(72.0)
    item.set_tile_size(100, 72)
    item.set_enabled(True)
    item.set_view_state(0.0, 400.0, 20.0, 30.0, repaint=False)
    requests = _Requests()
    item.on_missing_tiles = requests
    item.request_missing_tiles()
    focus = 10.0
    assert requests.visible
    visible_distance = [abs(time_s - focus) for time_s in requests.visible]
    assert visible_distance == sorted(visible_distance)
    assert requests.prefetch
    assert set(requests.visible).isdisjoint(requests.prefetch)
    prefetch_distance = [abs(time_s - focus) for time_s in requests.prefetch]
    assert prefetch_distance == sorted(prefetch_distance)


def test_ingest_frames_stops_repeat_requests() -> None:
    image = QImage(100, 72, QImage.Format.Format_RGB32)
    image.fill(0xFF224466)

    item = VideoFilmstripItem(72.0)
    item.set_tile_size(100, 72)
    item.set_enabled(True)
    item.set_view_state(0.0, 400.0, 20.0, 30.0, repaint=False)
    requests = _Requests()
    item.on_missing_tiles = requests
    item.request_missing_tiles()
    assert requests.visible
    first = requests.visible[0]
    requests.clear()
    item.ingest_frames([(first, image)])
    assert first not in requests.visible
    assert first not in requests.prefetch


def test_reopen_asks_again_after_a_dropped_request() -> None:
    item = VideoFilmstripItem(72.0)
    item.set_tile_size(100, 72)
    item.set_enabled(True)
    item.set_view_state(0.0, 400.0, 20.0, 30.0, repaint=False)
    requests = _Requests()
    item.on_missing_tiles = requests
    item.request_missing_tiles()
    assert requests.calls == 1
    item._failed.update([*requests.visible, *requests.prefetch])  # noqa: SLF001
    item.request_missing_tiles()
    assert requests.calls == 1

    item.reopen_filmstrip_holes()
    assert requests.calls == 2


def test_paint_does_not_rerequest_same_missing_times() -> None:
    item = VideoFilmstripItem(72.0)
    item.set_tile_size(100, 72)
    item.set_enabled(True)
    item.set_view_state(0.0, 400.0, 20.0, 30.0, repaint=False)
    requests = _Requests()
    item.on_missing_tiles = requests
    item.request_missing_tiles()
    _paint(item)
    _paint(item)
    item.request_missing_tiles()
    assert requests.calls == 1


def test_shrinking_lane_reflows_from_cache() -> None:
    image = QImage(100, 72, QImage.Format.Format_RGB32)
    image.fill(0xFF224466)

    item = VideoFilmstripItem(72.0)
    item.set_tile_size(100, 72)
    item.set_enabled(True)
    item.set_view_state(0.0, 400.0, 20.0, 30.0, repaint=False)
    requests = _Requests()
    item.on_missing_tiles = requests
    item.request_missing_tiles()
    assert len(requests.visible) == 4
    cached = requests.visible[0]
    item.ingest_frames([(cached, image)])

    requests.clear()
    item.set_lane_height(36.0)
    assert cached in item._pixmaps  # noqa: SLF001
    assert len(requests.visible) > 4
    shown = item._pixmap_for(requests.visible[0], max_delta=5.0)
    assert shown is not None
    assert not shown.isNull()


def test_nearest_cached_frame_is_used_across_zoom() -> None:
    image = QImage(100, 72, QImage.Format.Format_RGB32)
    image.fill(0xFF224466)

    item = VideoFilmstripItem(72.0)
    item.set_tile_size(100, 72)
    item.set_enabled(True)
    item.set_view_state(0.0, 400.0, 20.0, 30.0, repaint=False)
    requests = _Requests()
    item.on_missing_tiles = requests
    item.request_missing_tiles()
    assert requests.visible
    item.ingest_frames([(requests.visible[0], image)])
    item.set_view_state(0.0, 400.0, 23.0, 30.0, repaint=False)
    found = item._pixmap_for(requests.visible[0] + 0.2, max_delta=5.0)
    assert found is not None
    assert not found.isNull()


def test_nearest_cached_time_checks_neighbors() -> None:
    from corrcut.ui.timeline.filmstrip_item import nearest_cached_time

    times = [float(index) for index in range(200)]
    assert nearest_cached_time(times, 50.2, 1.0) == 50.0
    assert nearest_cached_time(times, 50.2, 0.1) is None
    spaced = [float(index) for index in range(0, 200, 4)]
    assert nearest_cached_time(spaced, 6.0, 2.0, rank_of=lambda time_s: int(time_s)) == 8.0
