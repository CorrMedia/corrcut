"""Resolve-style filmstrip tiles on the video timeline lane."""

from __future__ import annotations

import bisect
from collections import OrderedDict
from collections.abc import Callable, Sequence

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QImage, QPainter, QPixmap
from PySide6.QtWidgets import QGraphicsItem, QStyleOptionGraphicsItem

from corrcut.engine.filmstrip import (
    cover_crop_source,
    display_tile_width,
    filmstrip_priority_times,
    filmstrip_tiles,
    filmstrip_time_step,
)

MissingTilesCallback = Callable[[list[float], list[float]], None]
_ReportedMissing = tuple[tuple[float, ...], tuple[float, ...]]
_PIXMAP_CACHE_LIMIT = 256
_PLACEHOLDER = QColor(28, 28, 34)


def nearest_cached_time(
    times: Sequence[float],
    time_s: float,
    max_delta: float,
    *,
    rank_of: Callable[[float], int] | None = None,
) -> float | None:
    """Closest cached time within ``max_delta``.

    ``times`` is ascending. An equal distance keeps the higher rank, which is
    the frame most recently shown.
    """
    if max_delta < 0 or not times:
        return None
    index = bisect.bisect_left(times, time_s)
    best_t: float | None = None
    best_d = max_delta
    best_rank = -1
    for candidate in (index - 1, index):
        if candidate < 0 or candidate >= len(times):
            continue
        cached = times[candidate]
        delta = abs(cached - time_s)
        if delta > max_delta:
            continue
        rank = 0 if rank_of is None else rank_of(cached)
        if best_t is None or delta < best_d or (delta == best_d and rank >= best_rank):
            best_t = cached
            best_d = delta
            best_rank = rank
    return best_t


def _cover_source_rect(pixmap: QPixmap, dest: QRectF) -> QRectF:
    """Crop the still so it fills ``dest`` without stretching.

    Integer source and dest rects round independently and squash the frame by
    a pixel. Float rects keep the same aspect through the scale.
    """
    x, y, width, height = cover_crop_source(
        float(pixmap.width()),
        float(pixmap.height()),
        dest.width(),
        dest.height(),
    )
    return QRectF(x, y, max(1.0, width), max(1.0, height))


class VideoFilmstripItem(QGraphicsItem):
    """Renders visible cover-crop frames for the current scroll/zoom window."""

    def __init__(self, height: float) -> None:
        super().__init__()
        self._height = height
        self._duration = 1.0
        self._pps = 100.0
        self._scroll_x = 0.0
        self._viewport_width = 800.0
        self._tile_width = 128
        self._tile_height = max(8, int(round(height)))
        self._aspect = self._tile_width / float(self._tile_height)
        self._enabled = False
        self._fast_resize_preview = False
        self._pixmaps: OrderedDict[float, QPixmap] = OrderedDict()
        # Ascending times for nearest-frame lookup. Eviction still follows the OrderedDict.
        self._pixmap_times: list[float] = []
        self._pixmap_rank: dict[float, int] = {}
        self._pixmap_rank_next = 0
        self._failed: set[float] = set()
        self._reported_missing: _ReportedMissing = ((), ())
        self.on_missing_tiles: MissingTilesCallback | None = None
        self.setAcceptedMouseButtons(Qt.MouseButton.NoButton)
        self.setZValue(-1.5)

    def boundingRect(self) -> QRectF:  # noqa: N802
        return QRectF(0, 0, self._viewport_width, self._height)

    def set_lane_height(self, height: float) -> None:
        """Reflow tiles to the new lane height using frames already on screen."""
        if height == self._height:
            return
        self.prepareGeometryChange()
        self._height = height
        self._reported_missing = ((), ())
        self.update()
        self.request_missing_tiles()

    def set_view_state(
        self,
        scroll_x: float,
        viewport_width: float,
        pps: float,
        duration: float,
        *,
        repaint: bool = True,
    ) -> None:
        viewport_width = max(1.0, viewport_width)
        pps = max(1.0, pps)
        duration = max(0.001, duration)
        changed = (
            self._scroll_x != scroll_x
            or self._viewport_width != viewport_width
            or self._pps != pps
            or self._duration != duration
        )
        if self._viewport_width != viewport_width:
            self.prepareGeometryChange()
        self._scroll_x = scroll_x
        self._viewport_width = viewport_width
        self._pps = pps
        self._duration = duration
        if not changed:
            return
        if repaint:
            self.update()
        self.request_missing_tiles()

    def set_tile_size(self, width: int, height: int, *, aspect: float | None = None) -> None:
        width = max(8, int(width))
        height = max(8, int(height))
        ratio = float(aspect) if aspect is not None and aspect > 0 else width / float(height)
        if self._tile_width == width and self._tile_height == height and self._aspect == ratio:
            return
        size_changed = self._tile_width != width or self._tile_height != height
        self._tile_width = width
        self._tile_height = height
        self._aspect = ratio
        if size_changed:
            self._clear_pixmap_cache()
            self._failed.clear()
        self._reported_missing = ((), ())
        self.update()
        self.request_missing_tiles()

    def set_enabled(self, enabled: bool) -> None:
        if self._enabled == enabled:
            return
        self._enabled = enabled
        self.update()
        if enabled:
            self.request_missing_tiles()

    def reset(self) -> None:
        self._enabled = False
        self._clear_pixmap_cache()
        self._failed.clear()
        self._reported_missing = ((), ())
        self.update()

    def ingest_frames(
        self,
        frames: Sequence[tuple[float, QImage | QPixmap]],
        failed: Sequence[float] = (),
    ) -> None:
        """Install thumbnails decoded on the filmstrip worker.

        ``QPixmap.fromImage`` only uploads pixels. JPEG decode stays off this thread.
        """
        changed = False
        for time_s, image in frames:
            pixmap = image if isinstance(image, QPixmap) else QPixmap.fromImage(image)
            if pixmap.isNull():
                self._failed.add(time_s)
                changed = True
                continue
            self._failed.discard(time_s)
            self._store_pixmap(time_s, pixmap)
            changed = True
        for time_s in failed:
            if time_s not in self._pixmaps:
                self._failed.add(time_s)
                changed = True
        while len(self._pixmaps) > _PIXMAP_CACHE_LIMIT:
            evicted, _pixmap = self._pixmaps.popitem(last=False)
            self._drop_pixmap_time(evicted)
        if changed:
            self._reported_missing = ((), ())
            self.update()
            self.request_missing_tiles()

    def set_fast_resize_preview(self, enabled: bool) -> None:
        if self._fast_resize_preview == enabled:
            return
        self._fast_resize_preview = enabled
        self.update()

    def paint(self, painter: QPainter, option: QStyleOptionGraphicsItem, widget=None) -> None:  # noqa: ANN001, ARG002, N802
        exposed = self.boundingRect()
        if option is not None and not option.exposedRect.isEmpty():
            exposed = option.exposedRect.intersected(exposed)
        if exposed.isEmpty():
            return

        painter.fillRect(exposed, _PLACEHOLDER)
        if not self._enabled:
            return
        # Width drags skip new extracts, but cached stills still reflow with the lane.
        if self._fast_resize_preview and not self._pixmaps:
            return

        tile_width = self._display_tile_width()
        tiles = filmstrip_tiles(
            scroll_x=self._scroll_x,
            viewport_width=self._viewport_width,
            pps=self._pps,
            duration=self._duration,
            tile_width=tile_width,
        )
        step = filmstrip_time_step(tile_width / self._pps)
        # Old stills were sampled on the extracted tile grid. Keep showing the nearest
        # one while the lane height moves the grid, so the strip never goes blank.
        source_step = filmstrip_time_step(self._tile_width / self._pps)
        match_delta = max(step, source_step)
        scaling = abs(self._height - float(self._tile_height)) > 0.5
        if scaling:
            painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        painter.setPen(QColor(0, 0, 0, 90))
        for tile in tiles:
            dest = QRectF(tile.scene_x, 0.0, tile.width, self._height)
            pixmap = self._pixmap_for(tile.time_s, max_delta=match_delta)
            if pixmap is not None:
                painter.drawPixmap(dest, pixmap, _cover_source_rect(pixmap, dest))
            else:
                painter.fillRect(dest, _PLACEHOLDER)
            edge_x = tile.scene_x + tile.width
            if 0.0 <= edge_x <= self._viewport_width:
                painter.drawLine(int(edge_x), 0, int(edge_x), int(self._height))

        if scaling:
            painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, False)

    def request_missing_tiles(self) -> None:
        """Ask for stills the current scroll/zoom window does not have yet.

        Scroll and zoom call this. Paint only draws, so a playhead tick cannot
        schedule extracts.
        """
        if self._fast_resize_preview or not self._enabled or self.on_missing_tiles is None:
            return
        tile_width = self._display_tile_width()
        visible, prefetch = self._missing_priority(tile_width)
        reported = (tuple(visible), tuple(prefetch))
        if not visible and not prefetch:
            return
        if reported == self._reported_missing:
            return
        self._reported_missing = reported
        self.on_missing_tiles(visible, prefetch)

    def has_unfilled_tiles(self) -> bool:
        """True when the visible window is still missing thumbnails.

        Failed times count as holes. A failed extract used to stick until the
        view moved, which left black tiles on screen.
        """
        if not self._enabled:
            return False
        tile_width = self._display_tile_width()
        visible, _prefetch = filmstrip_priority_times(
            scroll_x=self._scroll_x,
            viewport_width=self._viewport_width,
            pps=self._pps,
            duration=self._duration,
            tile_width=tile_width,
        )
        return any(time_s not in self._pixmaps for time_s in visible)

    def reopen_filmstrip_holes(self) -> None:
        """Ask again after a load was cancelled or its frames were marked failed.

        The playhead only dirties a one-pixel strip, so also repaint the lane.
        """
        self._failed.clear()
        self._reported_missing = ((), ())
        self.update()
        self.request_missing_tiles()

    def _display_tile_width(self) -> float:
        return display_tile_width(self._height, self._aspect, 1.0)

    def _missing_priority(self, tile_width: float) -> tuple[list[float], list[float]]:
        visible, prefetch = filmstrip_priority_times(
            scroll_x=self._scroll_x,
            viewport_width=self._viewport_width,
            pps=self._pps,
            duration=self._duration,
            tile_width=tile_width,
        )
        return self._unloaded(visible), self._unloaded(prefetch)

    def _unloaded(self, times: list[float]) -> list[float]:
        missing: list[float] = []
        seen: set[float] = set()
        for time_s in times:
            if time_s in seen or time_s in self._pixmaps or time_s in self._failed:
                continue
            seen.add(time_s)
            missing.append(time_s)
        return missing

    def _pixmap_for(self, time_s: float, *, max_delta: float) -> QPixmap | None:
        exact = self._pixmaps.get(time_s)
        if exact is not None and not exact.isNull():
            self._touch_pixmap(time_s)
            return exact
        cached_t = nearest_cached_time(
            self._pixmap_times,
            time_s,
            max_delta,
            rank_of=self._rank_of,
        )
        if cached_t is None:
            return None
        pixmap = self._pixmaps.get(cached_t)
        if pixmap is None or pixmap.isNull():
            return None
        self._touch_pixmap(cached_t)
        return pixmap

    def _store_pixmap(self, time_s: float, pixmap: QPixmap) -> None:
        self._pixmaps[time_s] = pixmap
        self._touch_pixmap(time_s)
        index = bisect.bisect_left(self._pixmap_times, time_s)
        if index >= len(self._pixmap_times) or self._pixmap_times[index] != time_s:
            self._pixmap_times.insert(index, time_s)

    def _rank_of(self, time_s: float) -> int:
        return self._pixmap_rank.get(time_s, 0)

    def _touch_pixmap(self, time_s: float) -> None:
        self._pixmaps.move_to_end(time_s)
        self._pixmap_rank[time_s] = self._pixmap_rank_next
        self._pixmap_rank_next += 1

    def _drop_pixmap_time(self, time_s: float) -> None:
        index = bisect.bisect_left(self._pixmap_times, time_s)
        if index < len(self._pixmap_times) and self._pixmap_times[index] == time_s:
            del self._pixmap_times[index]
        self._pixmap_rank.pop(time_s, None)

    def _clear_pixmap_cache(self) -> None:
        self._pixmaps.clear()
        self._pixmap_times.clear()
        self._pixmap_rank.clear()
