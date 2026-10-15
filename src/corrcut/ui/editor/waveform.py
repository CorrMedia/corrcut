"""Waveform cache build and peak preload jobs."""

from __future__ import annotations

import logging

from PySide6.QtCore import QObject, QTimer, Signal

from corrcut.engine.peak_preload import (
    PeakPreloadDone,
    WaveformBuildDone,
    WaveformPeaksChunk,
)
from corrcut.engine.waveform import (
    PeakStore,
    build_waveform_cache,
    preload_peak_memory,
    store_needs_preload,
)

logger = logging.getLogger(__name__)


class _WaveformChunkBridge(QObject):
    """Marshal progressive peak slices from the worker thread onto the UI thread."""

    chunk = Signal(object)


class EditorWaveformMixin:
    def _cancel_background_jobs(self) -> None:
        self._scene_cuts_resume_after_user_job = False
        if self._ai_job_runner.is_busy:
            self._suggestions_panel.mark_job_cancelling()
        if self._subtitle_job_runner.is_busy:
            self._subtitles_panel.mark_job_cancelling()
        self._waveform_job_runner.cancel_active()
        self._filmstrip_job_runner.cancel_active()
        self._scene_cut_job_runner.cancel_active()
        self._job_runner.cancel_active()
        self._ai_job_runner.cancel_active()
        self._subtitle_job_runner.cancel_active()
        self._cancel_embedded_subtitle_extract()

    def _background_jobs_busy(self) -> bool:
        return any(
            runner.is_busy
            for runner in (
                self._waveform_job_runner,
                self._filmstrip_job_runner,
                self._scene_cut_job_runner,
                self._subtitle_extract_job_runner,
                self._subtitle_job_runner,
                self._ai_job_runner,
                self._job_runner,
            )
        )

    def _sync_background_job_cancel(self, _busy: bool = False) -> None:
        """Show status-bar Cancel while a background or long job is running."""
        busy = self._background_jobs_busy()
        self._background_cancel_btn.setVisible(busy)
        self._cancel_job_action.setEnabled(busy)
        long_jobs: list[str] = []
        if self._ai_job_runner.is_busy:
            long_jobs.append("AI detection")
        if self._subtitle_job_runner.is_busy:
            long_jobs.append("the subtitle job")
        other_prep = any(
            runner.is_busy
            for runner in (
                self._waveform_job_runner,
                self._filmstrip_job_runner,
                self._scene_cut_job_runner,
                self._subtitle_extract_job_runner,
                self._job_runner,
            )
        )
        if long_jobs and not other_prep:
            self._background_cancel_btn.setToolTip("Cancel " + " and ".join(long_jobs))
        else:
            self._background_cancel_btn.setToolTip("Cancel background jobs")

    def _ensure_waveform_refresh_timer(self) -> None:
        timer = getattr(self, "_waveform_refresh_timer", None)
        if timer is not None:
            return
        timer = QTimer(self)
        timer.setSingleShot(True)
        timer.setInterval(100)
        timer.timeout.connect(self._flush_waveform_refresh)
        self._waveform_refresh_timer = timer
        self._waveform_refresh_pending = False
        self._timeline.scroll_settled.connect(self._on_timeline_scroll_settled)

    def _request_waveform_refresh(self) -> None:
        """Coalesce progressive paints so decode can outrun the UI thread."""
        self._ensure_waveform_refresh_timer()
        if self._timeline.is_scroll_interacting():
            # Keep scrolling snappy; apply the latest peaks when the drag settles.
            self._waveform_refresh_pending = True
            return
        timer = self._waveform_refresh_timer
        if not timer.isActive():
            timer.start()

    def _on_timeline_scroll_settled(self) -> None:
        if not getattr(self, "_waveform_refresh_pending", False):
            return
        self._waveform_refresh_pending = False
        self._flush_waveform_refresh()

    def _flush_waveform_refresh(self) -> None:
        self._timeline.refresh_peaks_only()

    def _ensure_waveform(self) -> None:
        if self._project is None or self._project.source is None:
            self._open_waveform_gate_for_scene_cuts()
            return
        if getattr(self, "_waveform_build_disabled", False):
            self._open_waveform_gate_for_scene_cuts()
            return
        if not self._project.source.path.is_file():
            self._open_waveform_gate_for_scene_cuts()
            return
        track = self._project.selected_track()
        if track is None:
            self._open_waveform_gate_for_scene_cuts()
            return
        from corrcut.engine.waveform import _channel_key_for_track

        channel_key = _channel_key_for_track(track)
        store = PeakStore.open_or_none(
            self._paths.cache_dir,
            self._project.source.fingerprint,
            track.stream_index,
            channel_key,
        )
        if store is not None:
            self._attach_peak_store(store)
            self._open_waveform_gate_for_scene_cuts()
            return
        self._build_waveform()

    def _attach_peak_store(self, store: PeakStore) -> None:
        self._peak_store = store
        track = self._project.selected_track() if self._project is not None else None
        if track is None:
            self._peak_store_track_key = None
        else:
            from corrcut.engine.waveform import _channel_key_for_track

            self._peak_store_track_key = (track.stream_index, _channel_key_for_track(track))
        self._timeline.set_peak_store(store, self._duration)
        if store_needs_preload(store):
            self._start_peak_preload(store)

    def _start_peak_preload(self, store: PeakStore) -> None:
        if self._project is None:
            return
        if self._waveform_job_runner.is_busy:
            return
        token = self._peak_preload_token
        cancel = self._waveform_job_runner.cancel_event

        def _preload() -> PeakPreloadDone:
            return PeakPreloadDone(
                token=token,
                store=preload_peak_memory(store, cancel_event=cancel),
            )

        if not self._waveform_job_runner.run_callable("Preload waveform peaks", _preload):
            self._status.showMessage("Waveform preload pending…", 3000)

    def _build_waveform(self) -> None:
        if self._project is None or self._project.source is None:
            return
        if not self._project.source.path.is_file():
            return
        track = self._project.selected_track()
        if track is None:
            return
        if self._waveform_job_runner.is_busy:
            # Selection already moved. Retry when this job ends; another click on
            # the same track will not, because the stream index is already current.
            self._waveform_switch_pending = True
            return
        media = self._project.source.path
        fp = self._project.source.fingerprint
        cache = self._paths.cache_dir

        generation = self._waveform_generation
        cancel = self._waveform_job_runner.cancel_event
        duration = max(0.001, float(self._duration))
        building = PeakStore.create_building(
            cache,
            fp,
            track,
            duration_seconds=duration,
            sample_rate=track.sample_rate or 48000,
        )
        self._attach_peak_store(building)

        bridge = getattr(self, "_waveform_chunk_bridge", None)
        if bridge is None:
            bridge = _WaveformChunkBridge(self)
            bridge.chunk.connect(self._on_waveform_chunk)
            self._waveform_chunk_bridge = bridge

        def _on_chunk(start_peak: int, peaks: object) -> None:
            bridge.chunk.emit(
                WaveformPeaksChunk(
                    generation=generation,
                    start_peak=int(start_peak),
                    peaks=peaks,  # type: ignore[arg-type]
                )
            )

        def _build() -> WaveformBuildDone:
            store = build_waveform_cache(
                media,
                cache,
                fp,
                track,
                cancel_event=cancel,
                on_chunk=_on_chunk,
                store=building,
            )
            return WaveformBuildDone(generation=generation, store=store)

        if not self._waveform_job_runner.run_callable("Build waveform cache", _build):
            self._status.showMessage("Waveform build pending…", 3000)

    def _on_waveform_chunk(self, result: object) -> None:
        if not isinstance(result, WaveformPeaksChunk):
            return
        if result.generation != self._waveform_generation:
            return
        if self._peak_store is None:
            return
        # Worker already wrote into the shared building store; refresh the lane.
        if not getattr(self, "_waveform_gate_open_for_scene_cuts", False):
            self._open_waveform_gate_for_scene_cuts()
        self._request_waveform_refresh()

    def _on_waveform_job_result(self, result: object) -> None:
        if isinstance(result, PeakPreloadDone):
            if result.token != self._peak_preload_token or self._peak_store is not result.store:
                return
            self._timeline.refresh_peak_display()
            self._status.showMessage("Waveform display ready", 4000)
            return
        if isinstance(result, WaveformBuildDone):
            if (
                result.generation != self._waveform_generation
                or self._project is None
                or self._project.source is None
            ):
                return
            self._waveform_build_failures = 0
            self._attach_peak_store(result.store)
            self._open_waveform_gate_for_scene_cuts()
            self._status.showMessage("Waveform cache ready", 5000)
            return
        if isinstance(result, PeakStore):
            if self._project is None:
                return
            self._attach_peak_store(result)
            self._open_waveform_gate_for_scene_cuts()
            self._status.showMessage("Waveform cache ready", 5000)

    def _on_waveform_job_failed(self, message: str) -> None:
        failures = getattr(self, "_waveform_build_failures", 0) + 1
        self._waveform_build_failures = failures
        self._waveform_generation += 1
        logger.warning("Waveform job failed: %s", message)
        if failures >= 2:
            # Stop the busy-idle retry loop after a second failure.
            self._waveform_build_disabled = True
            self._status.showMessage(f"Waveform unavailable: {message}", 8000)
        else:
            self._waveform_switch_pending = True
            self._status.showMessage(f"Waveform interrupted, retrying: {message}", 8000)
        self._open_waveform_gate_for_scene_cuts()

    def _displayed_waveform_matches_track(self) -> bool:
        project = self._project
        if project is None:
            return True
        track = project.selected_track()
        if track is None:
            return True
        if self._peak_store is None:
            return False
        from corrcut.engine.waveform import _channel_key_for_track

        expected = (track.stream_index, _channel_key_for_track(track))
        return getattr(self, "_peak_store_track_key", None) == expected

    def _on_waveform_job_busy(self, busy: bool) -> None:
        if busy:
            self._status.showMessage("Waveform job running…", 2000)
            return
        pending = getattr(self, "_waveform_switch_pending", False)
        self._waveform_switch_pending = False
        store = self._peak_store
        incomplete = store is not None and bool(getattr(store, "is_building", False))
        if pending or incomplete or not self._displayed_waveform_matches_track():
            if not getattr(self, "_waveform_build_disabled", False):
                self._ensure_waveform()
                return
        if (
            self._project is not None
            and self._peak_store is not None
            and store_needs_preload(self._peak_store)
        ):
            self._start_peak_preload(self._peak_store)

    def _open_waveform_gate_for_scene_cuts(self) -> None:
        """Allow scene-cut detection once peaks are cached or first paint has landed."""
        self._waveform_gate_open_for_scene_cuts = True
        if getattr(self, "_scene_cuts_waiting_for_waveform", False):
            self._scene_cuts_waiting_for_waveform = False
            self._schedule_deferred(0, self._ensure_scene_cuts)
