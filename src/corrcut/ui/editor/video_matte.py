"""Lazy video-matte detection for letterbox-preserving zoom."""

from __future__ import annotations

import logging

from corrcut.domain.operation import OperationType
from corrcut.domain.video_matte import MATTE_DETECTOR, VideoMatteCache
from corrcut.engine.filmstrip import probe_has_video
from corrcut.engine.video_matte import DEFAULT_CROPDETECT_LIMIT, detect_video_matte

logger = logging.getLogger(__name__)


class EditorVideoMatteMixin:
    def _project_has_zoom_ops(self) -> bool:
        if self._project is None:
            return False
        return any(op.enabled and op.type == OperationType.ZOOM for op in self._project.operations)

    def _frame_size_from_project(self) -> tuple[int, int] | None:
        if self._project is None or self._project.source is None:
            return None
        probe = self._project.source.probe if isinstance(self._project.source.probe, dict) else {}
        streams = probe.get("video_streams") or []
        if not isinstance(streams, list) or not streams:
            return None
        first = streams[0]
        if not isinstance(first, dict):
            return None
        try:
            width = int(first.get("width") or 0)
            height = int(first.get("height") or 0)
        except (TypeError, ValueError):
            return None
        if width <= 0 or height <= 0:
            return None
        return width, height

    def _ensure_video_matte(self, *, force: bool = False) -> bool:
        """Detect and cache the active picture matte when letterbox-preserving zoom needs it.

        Returns True when the cached matte was refreshed.
        """
        if self._project is None or self._project.source is None:
            return False
        if not self._project.preserve_letterbox_on_zoom:
            return False
        if not force and not self._project_has_zoom_ops():
            return False
        if not self._project.source.path.is_file():
            return False
        probe = self._project.source.probe if isinstance(self._project.source.probe, dict) else {}
        if not probe_has_video(probe):
            return False

        fp = self._project.source.fingerprint
        cache = self._project.video_matte
        if (
            not force
            and cache is not None
            and cache.matches(fp, limit=DEFAULT_CROPDETECT_LIMIT, detector=MATTE_DETECTOR)
        ):
            return False

        if cache is not None:
            self._project.video_matte = None

        size = self._frame_size_from_project()
        if size is None:
            return False
        frame_w, frame_h = size
        duration = float(probe.get("duration_seconds") or 0.0) or None
        try:
            box = detect_video_matte(
                self._project.source.path,
                frame_w=frame_w,
                frame_h=frame_h,
                duration_s=duration,
                limit=DEFAULT_CROPDETECT_LIMIT,
            )
        except Exception as exc:
            logger.warning("Video matte detect failed: %s", exc)
            status = getattr(self, "_status", None)
            if status is not None:
                status.showMessage(f"Letterbox detect failed: {exc}", 6000)
            return False

        if box is None:
            # Successfully scanned; no baked-in bars.
            box = {"x": 0.0, "y": 0.0, "width": 1.0, "height": 1.0}
            status = getattr(self, "_status", None)
            if status is not None:
                status.showMessage("No letterbox bars detected", 4000)
        else:
            status = getattr(self, "_status", None)
            if status is not None:
                top_pct = 100.0 * float(box["y"])
                bottom_pct = 100.0 * (1.0 - float(box["y"]) - float(box["height"]))
                status.showMessage(
                    f"Letterbox matte: {top_pct:.1f}% top / {bottom_pct:.1f}% bottom",
                    5000,
                )

        self._project.video_matte = VideoMatteCache(
            x=float(box["x"]),
            y=float(box["y"]),
            width=float(box["width"]),
            height=float(box["height"]),
            limit=DEFAULT_CROPDETECT_LIMIT,
            fingerprint=dict(fp),
            detector=MATTE_DETECTOR,
        )
        self._notify_project_modified()
        return True

    def _ensure_video_matte_and_refresh(self) -> None:
        """Deferred entry: detect matte then refresh lavfi when the cache changes."""
        if getattr(self, "_preview_sync_suppressed", 0) > 0:
            return
        refreshed = self._ensure_video_matte()
        if refreshed and self._project is not None and self._project.selected_track() is not None:
            # Refresh filters only — do not re-enter ensure scheduling.
            self._preview_panel.sync_track_and_filters()
