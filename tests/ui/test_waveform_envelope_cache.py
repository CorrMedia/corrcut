"""Playback should reuse the waveform envelope until the view or peaks change."""

from types import SimpleNamespace

from PySide6.QtCore import QRectF
from PySide6.QtGui import QImage, QPainter
from PySide6.QtWidgets import QStyleOptionGraphicsItem

from corrcut.ui.timeline import waveform_item as waveform_module
from corrcut.ui.timeline.waveform_item import WaveformItem, envelope_path


class _Peaks:
    def __init__(
        self,
        peaks: list[tuple[float, float]],
        *,
        base_pps: int,
        levels: int,
    ) -> None:
        self._peaks = peaks
        self._base_pps = base_pps
        self.index = SimpleNamespace(levels=levels)

    def level_peaks_per_second(self, level: int) -> int:
        return max(1, self._base_pps // (2**level))

    def peaks_for_level_if_ready(self, level: int) -> list[tuple[float, float]]:
        del level
        return self._peaks


def _wave_x(image: QImage) -> int | None:
    """Device x of the first waveform pixel, so a scroll can be measured."""
    for x in range(image.width()):
        for y in range(image.height()):
            color = image.pixelColor(x, y)
            if color.alpha() > 40 and color.green() > color.red():
                return x
    return None


class _ScaleWidget:
    def __init__(self, ratio: float) -> None:
        self._ratio = ratio

    def devicePixelRatioF(self) -> float:
        return self._ratio


def _paint(item: WaveformItem, *, strip: bool, dpr: float = 1.0) -> QImage:
    bounds = item.boundingRect()
    image = QImage(int(bounds.width()), int(bounds.height()), QImage.Format.Format_ARGB32)
    image.fill(0)
    painter = QPainter(image)
    option = QStyleOptionGraphicsItem()
    exposed = item.boundingRect()
    if strip:
        exposed = QRectF(exposed.center().x(), 0.0, 1.0, exposed.height())
    option.exposedRect = exposed
    widget = _ScaleWidget(dpr) if dpr != 1.0 else None
    item.paint(painter, option, widget)
    painter.end()
    return image


def _loudest_column(image: QImage) -> float | None:
    """Center of the tallest waveform column, in logical pixels."""
    best = 0
    cols: list[int] = []
    for x in range(image.width()):
        span = 0
        for y in range(image.height()):
            color = image.pixelColor(x, y)
            if (
                color.alpha() > 40
                and color.green() > color.red() + 30
                and color.green() > color.blue() + 30
            ):
                span += 1
        if span > best:
            best = span
            cols = [x]
        elif span == best and span > 0:
            cols.append(x)
    if not cols or best < 4:
        return None
    return (cols[0] + cols[-1]) / 2.0


def test_envelope_is_rebuilt_only_when_view_or_level_changes(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    calls = {"n": 0}
    real = envelope_path

    def counted(points: list[tuple[float, float, float]]):
        calls["n"] += 1
        return real(points)

    monkeypatch.setattr(waveform_module, "envelope_path", counted)

    peaks = [(0.0, 0.4), (-0.2, 0.5), (-0.1, 0.2), (0.0, 0.8)]
    item = WaveformItem(80.0)
    item.set_peak_store(_Peaks(peaks, base_pps=200, levels=1))  # type: ignore[arg-type]
    item.set_view_state(0.0, 200.0, 100.0, 10.0, repaint=False)

    _paint(item, strip=False)
    cached = item._envelope_image  # noqa: SLF001
    assert not cached.isNull()
    origin = _wave_x(cached)
    assert origin is not None
    _paint(item, strip=True)
    assert calls["n"] == 1
    assert item._envelope_image is cached  # noqa: SLF001

    item.set_view_state(16.0, 200.0, 100.0, 10.0, repaint=False)
    _paint(item, strip=True)
    slid = _wave_x(item._envelope_image)  # noqa: SLF001
    assert slid is not None
    assert abs((origin - 16) - slid) <= 1

    before_zoom = calls["n"]
    item.set_view_state(16.0, 200.0, 150.0, 10.0, repaint=False)
    _paint(item, strip=True)
    assert calls["n"] > before_zoom

    before_height = calls["n"]
    item.set_lane_height(100.0)
    _paint(item, strip=True)
    assert calls["n"] > before_height

    before_store = calls["n"]
    item.set_peak_store(_Peaks(peaks, base_pps=800, levels=8))  # type: ignore[arg-type]
    _paint(item, strip=True)
    assert calls["n"] > before_store

    before_peaks = calls["n"]
    item.set_peak_store(_Peaks(list(peaks), base_pps=800, levels=8))  # type: ignore[arg-type]
    _paint(item, strip=True)
    assert calls["n"] > before_peaks


def _item_at(scroll: float) -> WaveformItem:
    peaks = [(0.0, 0.4), (-0.2, 0.5), (-0.1, 0.2), (0.0, 0.8)]
    item = WaveformItem(80.0)
    item.set_peak_store(_Peaks(peaks, base_pps=200, levels=1))  # type: ignore[arg-type]
    item.set_view_state(scroll, 200.0, 100.0, 10.0, repaint=False)
    _paint(item, strip=False)
    return item


def test_scroll_back_slides_the_ribbon_the_other_way() -> None:
    item = _item_at(0.0)
    origin = _wave_x(item._envelope_image)  # noqa: SLF001
    assert origin is not None

    item.set_view_state(16.0, 200.0, 100.0, 10.0, repaint=False)
    _paint(item, strip=False)
    item.set_view_state(0.0, 200.0, 100.0, 10.0, repaint=False)
    _paint(item, strip=False)
    returned = _wave_x(item._envelope_image)  # noqa: SLF001
    assert returned is not None
    assert abs(returned - origin) <= 1


def test_fast_resize_keeps_the_cached_ribbon() -> None:
    item = _item_at(0.0)
    cached = item._envelope_image  # noqa: SLF001
    item.set_fast_resize_preview(True)
    painted = _paint(item, strip=False)
    assert item._envelope_image is cached  # noqa: SLF001
    assert _wave_x(painted) is not None


def test_hidpi_scroll_keeps_the_ribbon_on_the_time_grid() -> None:
    """A logical source rect used to stretch the ribbon by the screen scale.

    On a scaled display the waveform then slid faster than the ruler once the
    playhead pinned and the timeline started moving.
    """
    peaks = [(0.0, 0.02)] * 40
    peaks[2] = (-1.0, 1.0)
    item = WaveformItem(80.0)
    item.set_peak_store(_Peaks(peaks, base_pps=200, levels=1))  # type: ignore[arg-type]
    item.set_view_state(0.0, 200.0, 100.0, 10.0, repaint=False)

    painted = _paint(item, strip=False, dpr=2.0)
    origin = _loudest_column(painted)
    # Peak 2 of 40 sits at (2.5 / 40) * (10s * 100 px/s).
    assert origin is not None
    assert abs(origin - 62.5) <= 4

    item.set_view_state(20.0, 200.0, 100.0, 10.0, repaint=False)
    slid = _loudest_column(_paint(item, strip=False, dpr=2.0))
    assert slid is not None
    assert abs(slid - (62.5 - 20.0)) <= 4
    assert abs((origin - slid) - 20.0) <= 4


def test_jump_wider_than_the_viewport_redraws() -> None:
    item = _item_at(0.0)
    item.set_view_state(250.0, 200.0, 100.0, 10.0, repaint=False)
    _paint(item, strip=False)

    fresh = _item_at(250.0)
    assert _wave_x(item._envelope_image) == _wave_x(fresh._envelope_image)  # noqa: SLF001
