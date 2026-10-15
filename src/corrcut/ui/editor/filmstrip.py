"""Video-lane filmstrip thumbnail jobs."""

from __future__ import annotations

import logging
from dataclasses import dataclass

from PySide6.QtGui import QImage, QImageReader

from corrcut.engine.filmstrip import (
    aspect_from_probe,
    ensure_filmstrip_frames,
    filmstrip_cache_dir,
    filmstrip_extract_pixel_size,
    plan_filmstrip_queue,
    probe_has_video,
    take_filmstrip_batch,
)
from corrcut.engine.jobs.cancel import check_cancelled
from corrcut.io.filmstrip_cache import touch_filmstrip_cache

logger = logging.getLogger(__name__)

_FILMSTRIP_BATCH = 12
_decoders_ready = False


@dataclass(frozen=True)
class FilmstripTilesReady:
    """Thumbnails decoded off the UI thread; generation invalidates stale results."""

    generation: int
    frames: tuple[tuple[float, QImage], ...]
    failed: tuple[float, ...]


def _split_filmstrip_request(payload: object) -> tuple[list[float], list[float]] | None:
    if not isinstance(payload, (list, tuple)) or len(payload) != 2:
        return None
    visible, prefetch = payload
    if not isinstance(visible, (list, tuple)) or not isinstance(prefetch, (list, tuple)):
        return None
    return [float(time_s) for time_s in visible], [float(time_s) for time_s in prefetch]


def _warm_thumbnail_decoders() -> None:
    """Load Qt image plugins on the UI thread before the worker reads JPEGs."""
    global _decoders_ready
    if _decoders_ready:
        return
    QImageReader.supportedImageFormats()
    _decoders_ready = True


class EditorFilmstripMixin:
    def _ensure_filmstrip(self) -> None:
        if self._project is None or self._project.source is None:
            self._timeline.clear_filmstrip()
            return
        if not self._project.source.path.is_file():
            self._timeline.clear_filmstrip()
            return
        probe = self._project.source.probe if isinstance(self._project.source.probe, dict) else {}
        if not probe_has_video(probe):
            self._timeline.clear_filmstrip()
            return
        aspect = aspect_from_probe(probe)
        tile_w, tile_h = filmstrip_extract_pixel_size(aspect)
        cache = filmstrip_cache_dir(self._paths.cache_dir, self._project.source.fingerprint)
        touch_filmstrip_cache(cache)
        self._timeline.set_filmstrip_source(tile_width=tile_w, tile_height=tile_h, aspect=aspect)

    def _on_filmstrip_tiles_needed(self, times: object) -> None:
        split = _split_filmstrip_request(times)
        if split is None:
            return
        visible, prefetch = split
        if not visible and not prefetch:
            return
        in_flight = list(getattr(self, "_filmstrip_in_flight", []))
        plan = plan_filmstrip_queue(visible, prefetch, in_flight)
        self._filmstrip_pending = list(plan.pending)
        self._filmstrip_primary_count = plan.primary_count
        runner = self._filmstrip_job_runner
        if plan.cancel and runner.is_busy:
            runner.cancel_active()
            return
        if runner.is_busy:
            return
        self._start_filmstrip_job()

    def _start_filmstrip_job(self) -> None:
        if self._project is None or self._project.source is None:
            return
        if not self._project.source.path.is_file():
            return
        runner = self._filmstrip_job_runner
        if runner.is_busy:
            return
        pending: list[float] = list(getattr(self, "_filmstrip_pending", []))
        primary = int(getattr(self, "_filmstrip_primary_count", 0))
        batch, pending, primary = take_filmstrip_batch(
            pending,
            primary,
            limit=_FILMSTRIP_BATCH,
        )
        if not batch:
            return
        media = self._project.source.path
        cache = filmstrip_cache_dir(self._paths.cache_dir, self._project.source.fingerprint)
        probe = self._project.source.probe if isinstance(self._project.source.probe, dict) else {}
        tile_w, tile_h = filmstrip_extract_pixel_size(aspect_from_probe(probe))
        duration = self._duration
        generation = self._filmstrip_generation
        cancel = runner.cancel_event
        _warm_thumbnail_decoders()

        def _extract() -> FilmstripTilesReady:
            touch_filmstrip_cache(cache)
            result = ensure_filmstrip_frames(
                media,
                cache,
                batch,
                width=tile_w,
                height=tile_h,
                duration=duration,
                cancel_event=cancel,
            )
            decoded: list[tuple[float, QImage]] = []
            failed = list(result.failed)
            for time_s, path in result.frames:
                check_cancelled(cancel)
                image = QImage(str(path))
                if image.isNull():
                    failed.append(time_s)
                    continue
                decoded.append((time_s, image.copy()))
            return FilmstripTilesReady(
                generation=generation,
                frames=tuple(decoded),
                failed=tuple(failed),
            )

        if not runner.run_callable("Filmstrip thumbnails", _extract):
            return
        self._filmstrip_in_flight = batch
        self._filmstrip_pending = pending
        self._filmstrip_primary_count = primary

    def _on_filmstrip_job_result(self, result: object) -> None:
        if not isinstance(result, FilmstripTilesReady):
            return
        if result.generation != self._filmstrip_generation:
            return
        self._filmstrip_in_flight = []
        if result.frames:
            self._filmstrip_hole_retries = 0
        self._timeline.ingest_filmstrip_frames(result.frames, result.failed)
        self._start_filmstrip_job()

    def _on_filmstrip_job_failed(self, message: str) -> None:
        self._filmstrip_in_flight = []
        logger.warning("Filmstrip job failed: %s", message)
        self._status.showMessage(f"Video thumbnails unavailable: {message}", 6000)

    def _on_filmstrip_job_busy(self, busy: bool) -> None:
        if busy:
            return
        self._filmstrip_in_flight = []
        if getattr(self, "_filmstrip_pending", None):
            self._start_filmstrip_job()
            return
        # A cancelled batch is removed from the queue, and the filmstrip will not
        # ask for those times again until the view moves. The playhead only
        # repaints a one-pixel strip, so the holes stay on screen.
        if getattr(self, "_filmstrip_hole_retries", 0) >= 2:
            return
        if not self._timeline.filmstrip_has_holes():
            return
        self._filmstrip_hole_retries = getattr(self, "_filmstrip_hole_retries", 0) + 1
        self._timeline.reopen_filmstrip_holes()
