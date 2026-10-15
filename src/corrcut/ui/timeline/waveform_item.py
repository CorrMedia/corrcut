"""Waveform peaks drawn from PeakStore as a Resolve-style clip envelope."""

from __future__ import annotations

import math

import numpy as np
from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QImage, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QGraphicsItem, QStyleOptionGraphicsItem, QWidget

from corrcut.engine.waveform import PeakStore
from corrcut.ui.timeline.timeline_lanes import REGION_PAD_Y

# Track / clip / envelope palette.
_TRACK_BED = QColor(0x1C, 0x1C, 0x22)
_CLIP_BODY = QColor(0x1C, 0x1C, 0x22)
_WAVE_FILL = QColor(61, 143, 74, 200)
_ZERO_LINE = QColor(255, 255, 255, 28)
_LOADING_TEXT = QColor(120, 120, 130)

# Use most of the clip height; clamp keeps ±1.5 peaks inside the inset.
_ENVELOPE_SCALE = 0.46


def peak_index_range(content_x: float, content_width: float, total_peaks: int) -> tuple[int, int]:
    """Map a content-pixel position to a [start, end) peak index range for one screen column."""
    if content_width <= 0 or total_peaks <= 0:
        return 0, 0
    start = int(content_x / content_width * total_peaks)
    end = min(total_peaks, max(start + 1, int((content_x + 1.0) / content_width * total_peaks)))
    return start, end


def clip_body_rect(
    scroll_x: float,
    pps: float,
    duration: float,
    height: float,
    *,
    inset_y: float = REGION_PAD_Y,
) -> QRectF:
    """Item-local rect for the source clip sitting on the audio track bed."""
    content_width = max(0.0, duration * pps)
    inset = min(inset_y, height / 4.0)
    return QRectF(-scroll_x, inset, content_width, max(1.0, height - 2.0 * inset))


def column_peak_minmax(
    content_x: float,
    content_width: float,
    peaks: list[tuple[float, float]],
) -> tuple[float, float] | None:
    """Min/max for one screen column, or None when the column has no usable peaks."""
    total = len(peaks)
    g_start, g_end = peak_index_range(content_x, content_width, total)
    if g_start >= total or g_start >= g_end:
        return None
    mn = math.inf
    mx = -math.inf
    for j in range(g_start, g_end):
        pmn, pmx = _sanitize_peak(peaks[j][0]), _sanitize_peak(peaks[j][1])
        mn = min(mn, pmn)
        mx = max(mx, pmx)
    if not math.isfinite(mn) or not math.isfinite(mx):
        return None
    return mn, mx


class WaveformItem(QGraphicsItem):
    """Renders only the visible viewport slice of the waveform."""

    def __init__(self, height: float) -> None:
        super().__init__()
        self._height = height
        self._duration = 1.0
        self._pps = 100.0
        self._scroll_x = 0.0
        self._viewport_width = 800.0
        self._peak_store: PeakStore | None = None
        self._fast_resize_preview = False
        # Playhead ticks expose a one-pixel strip. Rasterize the ribbon once.
        # Scrolling slides that image and draws only the edge coming into view.
        self._envelope_image = QImage()
        self._envelope_peaks: object | None = None
        self._envelope_key: tuple[object, ...] | None = None
        self._envelope_scroll: float | None = None
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemUsesExtendedStyleOption, True)
        self.setZValue(-1)

    def boundingRect(self) -> QRectF:  # noqa: N802
        return QRectF(0, 0, self._viewport_width, self._height)

    def set_lane_height(self, height: float) -> None:
        if height == self._height:
            return
        self.prepareGeometryChange()
        self._height = height
        self.update()

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
        # Width is the item's bounding rect. The scene index stays stale without this,
        # so a wider window never asks the item to paint the new strip.
        if self._viewport_width != viewport_width:
            self.prepareGeometryChange()
        self._scroll_x = scroll_x
        self._viewport_width = viewport_width
        self._pps = pps
        self._duration = duration
        if changed and repaint:
            self.update()

    def set_peak_store(self, store: PeakStore | None) -> None:
        if store is self._peak_store:
            return
        self._peak_store = store
        self._envelope_image = QImage()
        self._envelope_peaks = None
        self._envelope_key = None
        self._envelope_scroll = None
        self.update()

    def refresh_peaks(self) -> None:
        """Repaint after mip levels were loaded into the existing store."""
        if self._peak_store is None:
            return
        # Peak lists are mutated in place during progressive builds; drop the blit cache.
        self._envelope_image = QImage()
        self._envelope_peaks = None
        self._envelope_key = None
        self._envelope_scroll = None
        self.update()

    def set_fast_resize_preview(self, enabled: bool) -> None:
        """Skip a new peak pass while the dock width is changing.

        The last ribbon stays on screen. Rebuilding it on every resize tick
        drops frames, and a solid fill would wipe the waveform until the drag ends.
        """
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

        painter.fillRect(exposed, _TRACK_BED)
        clip = clip_body_rect(self._scroll_x, self._pps, self._duration, self._height)
        visible_clip = clip.intersected(exposed)
        if visible_clip.isEmpty():
            return

        painter.fillRect(visible_clip, _CLIP_BODY)
        if self._peak_store is None:
            return
        if self._fast_resize_preview:
            self._paint_cached_envelope(painter, visible_clip)
            return

        store = self._peak_store
        building = bool(getattr(store, "is_building", False))
        arrays = store.base_peak_arrays() if building and hasattr(store, "base_peak_arrays") else None
        if arrays is not None:
            peaks_arr, ready_mask = arrays
            if not store.has_displayable_peaks():
                painter.setPen(_LOADING_TEXT)
                painter.drawText(
                    visible_clip.toRect(),
                    Qt.AlignmentFlag.AlignCenter,
                    "Loading waveform…",
                )
                return
            content_width = self._duration * self._pps
            if content_width <= 0:
                return
            image = self._cached_envelope_image(
                0,
                peaks_arr,
                content_width,
                clip,
                _device_pixel_ratio(widget),
                self._height,
                ready_mask=ready_mask,
                ready_token=int(getattr(store, "ready_count", 0)),
            )
        else:
            level = _pick_level(store, self._pps)
            peaks = store.peaks_for_level_if_ready(level)
            displayable = getattr(store, "has_displayable_peaks", None)
            if peaks is None or (callable(displayable) and not displayable()):
                painter.setPen(_LOADING_TEXT)
                painter.drawText(
                    visible_clip.toRect(),
                    Qt.AlignmentFlag.AlignCenter,
                    "Loading waveform…",
                )
                return
            if not peaks:
                return
            content_width = self._duration * self._pps
            if content_width <= 0:
                return
            image = self._cached_envelope_image(
                level,
                peaks,
                content_width,
                clip,
                _device_pixel_ratio(widget),
                self._height,
            )
        if image.isNull():
            return
        painter.drawImage(visible_clip, image, _device_source_rect(image, visible_clip))

    def _cached_envelope_image(
        self,
        level: int,
        peaks: object,
        content_width: float,
        clip: QRectF,
        device_pixel_ratio: float,
        lane_height: float,
        *,
        ready_mask: object | None = None,
        ready_token: int | None = None,
    ) -> QImage:
        if ready_token is None:
            ready_token = int(ready_mask.sum()) if ready_mask is not None else -1
        key = (
            self._viewport_width,
            self._pps,
            self._duration,
            self._height,
            level,
            device_pixel_ratio,
            ready_token,
        )
        scroll = self._scroll_x
        if (
            self._envelope_key == key
            and self._envelope_peaks is peaks
            and self._envelope_scroll is not None
            and not self._envelope_image.isNull()
        ):
            if self._envelope_scroll == scroll:
                return self._envelope_image
            slid = _slide_envelope(
                self._envelope_image,
                old_scroll=self._envelope_scroll,
                new_scroll=scroll,
                viewport_width=self._viewport_width,
                peaks=peaks,
                content_width=content_width,
                clip=clip,
                ready_mask=ready_mask,
            )
            if slid is not None:
                self._envelope_image = slid
                self._envelope_scroll = scroll
                return slid

        image = _rasterize_envelope(
            peaks,
            content_width=content_width,
            scroll_x=scroll,
            viewport_width=self._viewport_width,
            lane_height=lane_height,
            clip=clip,
            device_pixel_ratio=device_pixel_ratio,
            ready_mask=ready_mask,
        )
        self._envelope_key = key
        self._envelope_peaks = peaks  # type: ignore[assignment]
        self._envelope_scroll = scroll
        self._envelope_image = image
        return image

    def _paint_cached_envelope(self, painter: QPainter, visible_clip: QRectF) -> None:
        """Blit the last ribbon. A size or scroll mismatch waits for the full rebuild."""
        image = self._envelope_image
        if image.isNull() or self._envelope_scroll != self._scroll_x:
            return
        ratio = image.devicePixelRatio()
        if ratio <= 0.0:
            ratio = 1.0
        if abs(image.width() / ratio - self._viewport_width) > 1.0:
            return
        if abs(image.height() / ratio - self._height) > 1.0:
            return
        painter.drawImage(visible_clip, image, _device_source_rect(image, visible_clip))


def visible_peak_span(
    scroll_x: float,
    viewport_width: float,
    content_width: float,
    total_peaks: int,
    *,
    pad: int = 1,
) -> tuple[int, int]:
    """Inclusive-start, exclusive-end peak indices that cover the viewport (plus pad)."""
    if content_width <= 0 or total_peaks <= 0:
        return 0, 0
    start = int(math.floor(scroll_x / content_width * total_peaks)) - pad
    end = int(math.ceil((scroll_x + viewport_width) / content_width * total_peaks)) + pad
    start = max(0, start)
    end = min(total_peaks, max(start + 1, end))
    return start, end


def peak_center_x(index: int, total_peaks: int, content_width: float, scroll_x: float) -> float:
    """Item-local X of a peak sample's time center."""
    if total_peaks <= 0:
        return -scroll_x
    return (index + 0.5) / total_peaks * content_width - scroll_x


def pick_peak_level(base_pps: int, levels: int, screen_pps: float) -> int:
    """Coarsest mip that still has at least one peak per screen pixel."""
    screen_pps = max(1.0, screen_pps)
    chosen = 0
    for level in range(max(1, levels)):
        if max(1, base_pps // (2**level)) >= screen_pps:
            chosen = level
        else:
            break
    return chosen


def _envelope_y(
    pair: tuple[float, float],
    mid_y: float,
    scale: float,
    clip_top: float,
    clip_bottom: float,
) -> tuple[float, float]:
    mn, mx = pair
    top = _clamp(mid_y - mx * scale, clip_top, clip_bottom)
    bottom = _clamp(mid_y - mn * scale, clip_top, clip_bottom)
    bar_top = min(top, bottom)
    bar_bottom = max(top, bottom)
    if bar_bottom - bar_top < 1.0:
        mid = (bar_top + bar_bottom) * 0.5
        bar_top = _clamp(mid - 0.5, clip_top, clip_bottom)
        bar_bottom = _clamp(mid + 0.5, clip_top, clip_bottom)
    return bar_top, bar_bottom


def _device_pixel_ratio(widget: QWidget | None) -> float:
    if widget is None:
        return 1.0
    ratio = float(widget.devicePixelRatioF())
    return ratio if ratio > 0.0 else 1.0


def _device_source_rect(image: QImage, logical: QRectF) -> QRectF:
    """Map a logical clip to raw image pixels.

    The ribbon is stored at the screen scale so peaks stay sharp. ``drawImage``
    reads its source rect in those raw pixels and does not apply the image's
    device pixel ratio. A logical clip therefore samples too few columns and
    stretches the ribbon by that ratio, so it races ahead of the ruler and
    filmstrip once the timeline scrolls.
    """
    ratio = image.devicePixelRatio()
    if ratio <= 0.0 or ratio == 1.0:
        return logical
    return QRectF(
        logical.x() * ratio,
        logical.y() * ratio,
        logical.width() * ratio,
        logical.height() * ratio,
    )


# Redraw a couple of pixels of the edge that slid in so the ribbon meets the copy.
_SLIDE_OVERLAP_PX = 2.0


def _rasterize_envelope(
    peaks: object,
    *,
    content_width: float,
    scroll_x: float,
    viewport_width: float,
    lane_height: float,
    clip: QRectF,
    device_pixel_ratio: float,
    ready_mask: object | None = None,
) -> QImage:
    """Draw the visible ribbon into an image the size of the waveform item."""
    ratio = max(1.0, device_pixel_ratio)
    width = max(1, int(math.ceil(viewport_width * ratio)))
    height = max(1, int(math.ceil(lane_height * ratio)))
    image = QImage(width, height, QImage.Format.Format_ARGB32_Premultiplied)
    image.setDevicePixelRatio(ratio)
    image.fill(Qt.GlobalColor.transparent)
    _paint_envelope(
        image,
        peaks,
        content_width=content_width,
        scroll_x=scroll_x,
        viewport_width=viewport_width,
        clip=clip,
        ready_mask=ready_mask,
        span=None,
    )
    return image


def _slide_envelope(
    image: QImage,
    *,
    old_scroll: float,
    new_scroll: float,
    viewport_width: float,
    peaks: object,
    content_width: float,
    clip: QRectF,
    ready_mask: object | None,
) -> QImage | None:
    """Move a cached ribbon by the scroll delta and draw the new edge.

    Returns None when the jump is wider than the viewport and the whole ribbon
    has to be drawn again.
    """
    ratio = image.devicePixelRatio()
    if ratio <= 0.0:
        ratio = 1.0
    shift = int(round(new_scroll * ratio)) - int(round(old_scroll * ratio))
    width = image.width()
    height = image.height()
    if width <= 0 or height <= 0 or abs(shift) >= width:
        return None
    if shift == 0:
        return image

    raw = image.copy()
    raw.setDevicePixelRatio(1.0)
    moved = QImage(width, height, image.format())
    moved.fill(Qt.GlobalColor.transparent)
    painter = QPainter(moved)
    if shift > 0:
        painter.drawImage(0, 0, raw, shift, 0, width - shift, height)
    else:
        painter.drawImage(-shift, 0, raw, 0, 0, width + shift, height)
    painter.end()
    moved.setDevicePixelRatio(ratio)

    dx = new_scroll - old_scroll
    if dx > 0:
        left = max(0.0, viewport_width - dx - _SLIDE_OVERLAP_PX)
        span = (left, viewport_width)
    else:
        right = min(viewport_width, -dx + _SLIDE_OVERLAP_PX)
        span = (0.0, right)
    _paint_envelope(
        moved,
        peaks,
        content_width=content_width,
        scroll_x=new_scroll,
        viewport_width=viewport_width,
        clip=clip,
        ready_mask=ready_mask,
        span=span,
    )
    return moved


def _paint_envelope(
    image: QImage,
    peaks: object,
    *,
    content_width: float,
    scroll_x: float,
    viewport_width: float,
    clip: QRectF,
    ready_mask: object | None,
    span: tuple[float, float] | None,
) -> None:
    """Draw the ribbon into *image*. *span* limits the draw to an item-local x range."""
    ratio = image.devicePixelRatio()
    if ratio <= 0.0:
        ratio = 1.0
    if span is None:
        origin = scroll_x
        span_width = viewport_width
        clip_rect = clip
        max_points = max(64, image.width() * 2)
    else:
        origin = scroll_x + span[0]
        span_width = max(1.0, span[1] - span[0])
        clip_rect = QRectF(span[0], clip.top(), span_width, clip.height())
        max_points = max(8, int(math.ceil(span_width * ratio)) * 2)

    total = _peaks_len(peaks)
    start, end = visible_peak_span(origin, span_width, content_width, total)
    mid_y = clip.center().y()
    scale = clip.height() * _ENVELOPE_SCALE
    segments = _envelope_segments(
        peaks,
        start=start,
        end=end,
        total=total,
        content_width=content_width,
        scroll_x=scroll_x,
        mid_y=mid_y,
        scale=scale,
        clip_top=clip.top(),
        clip_bottom=clip.bottom(),
        ready_mask=ready_mask,
        max_points=max_points,
    )

    painter = QPainter(image)
    painter.setClipRect(clip_rect)
    painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_Source)
    painter.fillRect(clip_rect, Qt.GlobalColor.transparent)
    painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceOver)
    # The ribbon is a dense min/max fill. Smoothing would blur the spikes editors aim at.
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, False)
    painter.setPen(QPen(_ZERO_LINE, 1.0))
    painter.drawLine(clip_rect.left(), mid_y, clip_rect.right(), mid_y)
    for points in segments:
        path = envelope_path(points)
        if not path.isEmpty():
            painter.fillPath(path, _WAVE_FILL)
    painter.end()


def _peaks_len(peaks: object) -> int:
    if hasattr(peaks, "shape"):
        shape = peaks.shape
        if shape:
            return int(shape[0])
    return len(peaks)  # type: ignore[arg-type]


def _peak_pair(peaks: object, index: int) -> tuple[float, float]:
    sample = peaks[index]  # type: ignore[index]
    if hasattr(sample, "__len__") and not isinstance(sample, tuple):
        return float(sample[0]), float(sample[1])
    return float(sample[0]), float(sample[1])


def _envelope_segments(
    peaks: object,
    *,
    start: int,
    end: int,
    total: int,
    content_width: float,
    scroll_x: float,
    mid_y: float,
    scale: float,
    clip_top: float,
    clip_bottom: float,
    ready_mask: object | None,
    max_points: int,
) -> list[list[tuple[float, float, float]]]:
    span = max(0, end - start)
    if span <= 0 or total <= 0:
        return []
    buckets = span if span <= max_points else max_points
    if (
        hasattr(peaks, "shape")
        and ready_mask is not None
        and hasattr(ready_mask, "shape")
    ):
        return _envelope_segments_numpy(
            peaks,  # type: ignore[arg-type]
            ready_mask,  # type: ignore[arg-type]
            start=start,
            end=end,
            total=total,
            buckets=buckets,
            content_width=content_width,
            scroll_x=scroll_x,
            mid_y=mid_y,
            scale=scale,
            clip_top=clip_top,
            clip_bottom=clip_bottom,
        )
    segments: list[list[tuple[float, float, float]]] = []
    current: list[tuple[float, float, float]] = []
    for bucket in range(buckets):
        p0 = start + (bucket * span) // buckets
        p1 = start + ((bucket + 1) * span) // buckets
        if p1 <= p0:
            p1 = min(end, p0 + 1)
        pair = _bucket_minmax(peaks, p0, p1, ready_mask)
        if pair is None:
            if current:
                segments.append(current)
                current = []
            continue
        mn, mx = _sanitize_peak(pair[0]), _sanitize_peak(pair[1])
        mid_index = (p0 + p1 - 1) * 0.5
        x = (mid_index + 0.5) / total * content_width - scroll_x
        top, bottom = _envelope_y((mn, mx), mid_y, scale, clip_top, clip_bottom)
        current.append((x, top, bottom))
    if current:
        segments.append(current)
    return segments


def _envelope_segments_numpy(
    peaks: np.ndarray,
    ready_mask: np.ndarray,
    *,
    start: int,
    end: int,
    total: int,
    buckets: int,
    content_width: float,
    scroll_x: float,
    mid_y: float,
    scale: float,
    clip_top: float,
    clip_bottom: float,
) -> list[list[tuple[float, float, float]]]:
    """One-pass bucket reduce — keeps scroll paints off the Python peak loop."""
    span = end - start
    if span <= 0 or buckets <= 0:
        return []
    block = peaks[start:end]
    ready = ready_mask[start:end]
    if not np.any(ready):
        return []
    # Map each sample in the span to a display bucket.
    bucket_ids = (np.arange(span, dtype=np.int32) * buckets) // span
    mins = np.full(buckets, np.inf, dtype=np.float64)
    maxs = np.full(buckets, -np.inf, dtype=np.float64)
    valid = np.nonzero(ready)[0]
    if valid.size == 0:
        return []
    np.minimum.at(mins, bucket_ids[valid], block[valid, 0])
    np.maximum.at(maxs, bucket_ids[valid], block[valid, 1])

    segments: list[list[tuple[float, float, float]]] = []
    current: list[tuple[float, float, float]] = []
    for bucket in range(buckets):
        if not np.isfinite(mins[bucket]):
            if current:
                segments.append(current)
                current = []
            continue
        p0 = start + (bucket * span) // buckets
        p1 = start + ((bucket + 1) * span) // buckets
        if p1 <= p0:
            p1 = min(end, p0 + 1)
        mn, mx = _sanitize_peak(float(mins[bucket])), _sanitize_peak(float(maxs[bucket]))
        mid_index = (p0 + p1 - 1) * 0.5
        x = (mid_index + 0.5) / total * content_width - scroll_x
        top, bottom = _envelope_y((mn, mx), mid_y, scale, clip_top, clip_bottom)
        current.append((x, top, bottom))
    if current:
        segments.append(current)
    return segments


def _bucket_minmax(
    peaks: object,
    start: int,
    end: int,
    ready_mask: object | None,
) -> tuple[float, float] | None:
    mn = math.inf
    mx = -math.inf
    found = False
    for index in range(start, end):
        if ready_mask is not None:
            if index >= len(ready_mask) or not bool(ready_mask[index]):  # type: ignore[arg-type]
                continue
        sample_mn, sample_mx = _peak_pair(peaks, index)
        if not (math.isfinite(sample_mn) and math.isfinite(sample_mx)):
            continue
        mn = min(mn, sample_mn)
        mx = max(mx, sample_mx)
        found = True
    if not found:
        return None
    return mn, mx


def envelope_path(points: list[tuple[float, float, float]]) -> QPainterPath:
    """Closed min/max ribbon connecting sample centers. Each item is (x, top, bottom)."""
    path = QPainterPath()
    if not points:
        return path
    if len(points) == 1:
        x, top, bottom = points[0]
        points = [(x - 0.5, top, bottom), (x + 0.5, top, bottom)]
    path.moveTo(points[0][0], points[0][1])
    for x, top, _bottom in points[1:]:
        path.lineTo(x, top)
    for x, _top, bottom in reversed(points):
        path.lineTo(x, bottom)
    path.closeSubpath()
    return path


def _sanitize_peak(value: float) -> float:
    if not math.isfinite(value):
        return 0.0
    return max(-1.5, min(1.5, value))


def _clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, value))


def _pick_level(store: PeakStore, pps: float) -> int:
    return pick_peak_level(store.level_peaks_per_second(0), store.index.levels, pps)
