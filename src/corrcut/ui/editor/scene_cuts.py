"""Background scene-cut detection for timeline magnetic snap."""

from __future__ import annotations

import logging

from corrcut.domain.scene_cuts import SceneCutCache
from corrcut.engine.filmstrip import probe_has_video
from corrcut.engine.scene_cuts import (
    DEFAULT_THRESHOLD,
    SceneCutDetectDone,
    detect_scene_cuts,
)

logger = logging.getLogger(__name__)


class EditorSceneCutsMixin:
    def _ensure_scene_cuts(self) -> None:
        if self._project is None or self._project.source is None:
            self._timeline.set_scene_cut_times(())
            return
        if not self._project.source.path.is_file():
            self._timeline.set_scene_cut_times(())
            return
        probe = self._project.source.probe if isinstance(self._project.source.probe, dict) else {}
        if not probe_has_video(probe):
            self._timeline.set_scene_cut_times(())
            return

        cache = self._project.scene_cuts
        fp = self._project.source.fingerprint
        if cache is not None and cache.matches(fp, threshold=DEFAULT_THRESHOLD):
            self._timeline.set_scene_cut_times(tuple(cache.times))
            return

        if cache is not None:
            self._project.scene_cuts = None
            self._notify_project_modified()
        self._timeline.set_scene_cut_times(())

        if not getattr(self, "_waveform_gate_open_for_scene_cuts", False):
            # Filmstrip stays ahead of waveform; scene cuts wait for first peaks.
            self._scene_cuts_waiting_for_waveform = True
            return

        if getattr(self, "_prep_held_for_user_job", False):
            self._scene_cuts_resume_after_user_job = True
            return

        runner = self._scene_cut_job_runner
        if runner.is_busy:
            return
        self._start_scene_cut_job()

    def _start_scene_cut_job(self) -> None:
        if self._project is None or self._project.source is None:
            return
        if not self._project.source.path.is_file():
            return
        probe = self._project.source.probe if isinstance(self._project.source.probe, dict) else {}
        if not probe_has_video(probe):
            return
        runner = self._scene_cut_job_runner
        if getattr(self, "_prep_held_for_user_job", False):
            self._scene_cuts_resume_after_user_job = True
            return
        if runner.is_busy:
            return

        media = self._project.source.path
        fingerprint = dict(self._project.source.fingerprint)
        generation = self._scene_cut_generation
        cancel = runner.cancel_event
        threshold = DEFAULT_THRESHOLD
        duration_hint = float(self._duration) if getattr(self, "_duration", 0) else None
        status = self._status

        def _progress(percent: int, message: str) -> None:
            # Worker thread → UI via queued invoke on the status bar.
            from PySide6.QtCore import Q_ARG, QMetaObject, Qt

            QMetaObject.invokeMethod(
                status,
                "showMessage",
                Qt.ConnectionType.QueuedConnection,
                Q_ARG(str, message),
                Q_ARG(int, 0),
            )
            del percent

        def _detect() -> SceneCutDetectDone:
            # Software decode. A hardware decode of the whole title contends
            # with mpv for the GPU and makes the window look frozen on open.
            times = detect_scene_cuts(
                media,
                threshold=threshold,
                cancel_event=cancel,
                progress=_progress,
                duration_hint_s=duration_hint,
                software=True,
            )
            return SceneCutDetectDone(
                generation=generation,
                fingerprint=fingerprint,
                threshold=threshold,
                times=tuple(times),
            )

        if not runner.run_callable("Detect scene cuts", _detect):
            return
        self._status.showMessage("Detecting scene cuts…", 0)

    def _on_scene_cut_job_result(self, result: object) -> None:
        if not isinstance(result, SceneCutDetectDone):
            return
        if result.generation != self._scene_cut_generation:
            return
        if self._project is None or self._project.source is None:
            return
        if not fingerprint_still_matches(self._project.source.fingerprint, result.fingerprint):
            return
        self._project.scene_cuts = SceneCutCache(
            threshold=result.threshold,
            fingerprint=dict(result.fingerprint),
            times=list(result.times),
        )
        self._timeline.set_scene_cut_times(result.times)
        self._notify_project_modified()
        n = len(result.times)
        label = "cut" if n == 1 else "cuts"
        self._status.showMessage(f"Found {n} scene {label}", 5000)

    def _on_scene_cut_job_failed(self, message: str) -> None:
        logger.warning("Scene-cut job failed: %s", message)
        self._status.showMessage(f"Scene cuts unavailable: {message}", 6000)

    def _on_scene_cut_job_cancelled(self) -> None:
        if getattr(self, "_scene_cuts_resume_after_user_job", False):
            return
        self._status.showMessage("Scene-cut detection cancelled", 3000)

    def _hold_prep_for_user_job(self) -> None:
        """Pause full-title scene-cut decode while a user job uses the file.

        Scene cuts already run on their own thread. Pausing them keeps that
        decode from competing with OCR or Whisper, then detection starts again
        when the user job goes idle.
        """
        self._prep_held_for_user_job = True
        if not self._scene_cut_job_runner.is_busy:
            return
        self._scene_cuts_resume_after_user_job = True
        self._scene_cut_generation += 1
        self._scene_cut_job_runner.cancel_active()

    def _release_prep_after_user_job(self) -> None:
        if self._subtitle_job_runner.is_busy or self._ai_job_runner.is_busy:
            return
        if not getattr(self, "_prep_held_for_user_job", False):
            return
        self._prep_held_for_user_job = False
        if not getattr(self, "_scene_cuts_resume_after_user_job", False):
            return
        if self._scene_cut_job_runner.is_busy:
            return
        self._scene_cuts_resume_after_user_job = False
        self._schedule_deferred(0, self._ensure_scene_cuts)

    def _on_scene_cut_job_busy(self, busy: bool) -> None:
        if busy:
            self._status.showMessage("Detecting scene cuts…", 0)
            return
        if getattr(self, "_prep_held_for_user_job", False):
            return
        if not getattr(self, "_scene_cuts_resume_after_user_job", False):
            return
        self._scene_cuts_resume_after_user_job = False
        self._schedule_deferred(0, self._ensure_scene_cuts)


def fingerprint_still_matches(
    current: dict[str, object],
    detected: dict[str, object],
) -> bool:
    from corrcut.domain.relink import fingerprint_matches

    return fingerprint_matches(current, detected)
