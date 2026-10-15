"""AI profanity detection job coordination."""

from __future__ import annotations

import logging

from PySide6.QtWidgets import QMessageBox

from corrcut.ai.detection_result import DetectionResult
from corrcut.ai.pipeline import merge_suggestions_for_track, run_detection
from corrcut.ai.transcribe import is_whisper_available, whisper_install_hint
from corrcut.config import env_value
from corrcut.domain.ai_suggestion import DetectionSettings
from corrcut.domain.project import Project
from corrcut.engine.subtitles.ingest import ingest_whisper_subtitles
from corrcut.engine.subtitles.service import store_whisper_cache
from corrcut.engine.toolchain import check_toolchain
from corrcut.io.project_store import save_project

logger = logging.getLogger(__name__)


class EditorAiMixin:
    _ai_job_project: Project | None
    _quit_in_progress: bool

    def _generate_suggestions_from_menu(self) -> None:
        self._generate_suggestions(self._suggestions_panel.detection_settings())

    def _generate_suggestions(self, settings: object) -> None:
        if self._project is None or self._project.source is None:
            QMessageBox.information(self, "AI", "Open media before running detection.")
            return
        if not self._project.source.path.is_file():
            QMessageBox.information(
                self,
                "AI",
                "Media file is missing. Use File → Relink media…",
            )
            return
        if self._project.selected_track() is None:
            QMessageBox.information(self, "AI", "Select an audio track to analyze.")
            return
        if self._ai_job_runner.is_busy:
            QMessageBox.information(self, "AI", "Wait for the current AI job to finish.")
            return
        if not isinstance(settings, DetectionSettings):
            settings = self._suggestions_panel.detection_settings()

        if env_value("AI_PYTHON") is None and not is_whisper_available():
            QMessageBox.warning(
                self,
                "AI not available",
                f"faster-whisper is not installed.\n\n{whisper_install_hint()}\n\n"
                "Or set CORRCUT_AI_PYTHON to a separate venv Python.",
            )
            return

        toolchain = check_toolchain()
        ffmpeg_ok = any(t.name == "ffmpeg" and t.ok for t in toolchain.tools)
        if not ffmpeg_ok:
            QMessageBox.warning(
                self, "AI", "ffmpeg is required to extract audio for transcription."
            )
            return

        project = self._project
        track = project.selected_track()
        assert track is not None
        cancel = self._ai_job_runner.cancel_event
        heading = f"Analyzing stream #{track.stream_index} ({track.codec})"
        self._ai_job_project = project
        self._hold_prep_for_user_job()
        self._suggestions_panel.show_job_banner(heading)
        self._status.showMessage(heading, 0)

        def _run() -> DetectionResult:
            return run_detection(
                project,
                settings,
                cache_dir=self._paths.cache_dir,
                cancel_event=cancel,
                log=lambda m: self._ai_job_runner.job_log.emit(m),
                progress=lambda p, m: self._ai_job_runner.job_progress.emit(p, m),
            )

        if not self._ai_job_runner.run_callable("AI detection", _run):
            self._ai_job_project = None
            self._release_prep_after_user_job()
            self._suggestions_panel.hide_job_banner()
            self._suggestions_panel.set_busy(False)
            self._status.showMessage("Could not start detection job", 4000)
            QMessageBox.information(self, "AI", "Could not start detection job.")

    def _on_suggestions_job_cancel(self) -> None:
        self._suggestions_panel.mark_job_cancelling()
        self._ai_job_runner.cancel_active()
        self._status.showMessage("Cancelling AI detection…", 0)

    def _on_ai_job_log(self, message: str) -> None:
        self._suggestions_panel.note_job_detail(message)

    def _on_ai_job_progress(self, percent: int, message: str) -> None:
        self._suggestions_panel.set_job_progress(percent, message)
        detail = message.strip() or "AI detection"
        self._status.showMessage(f"{percent}% {detail}", 0)

    def _release_ai_job(self) -> Project | None:
        project = getattr(self, "_ai_job_project", None)
        self._ai_job_project = None
        self._suggestions_panel.hide_job_banner()
        if project is None or self._project is not project:
            return None
        return project

    def _on_ai_job_result(self, result: object) -> None:
        if self._quit_in_progress:
            self._release_ai_job()
            return
        project = self._release_ai_job()
        if project is None or not isinstance(result, DetectionResult):
            return
        track = project.selected_track()
        if track is None:
            return
        if result.whisper_words:
            store_whisper_cache(
                project,
                stream_index=track.stream_index,
                model=self._suggestions_panel.detection_settings().whisper_model,
                words=result.whisper_words,
            )
            if ingest_whisper_subtitles(project):
                self._refresh_subtitle_ui()
        suggestions = list(result.suggestions)
        slop = self._suggestions_panel.detection_settings().cross_source_merge_slop_ms
        before_suggestions = list(project.ai_suggestions)
        merge_suggestions_for_track(project, suggestions, track.stream_index, merge_slop_ms=slop)
        after_suggestions = list(project.ai_suggestions)
        self._refresh_suggestions_panel()
        self._suggestions_dock.raise_()
        self._notify_project_modified()
        if before_suggestions != after_suggestions:
            self._push_undo(
                "AI detection",
                undo=lambda: self._restore_ai_suggestions(before_suggestions),
                redo=lambda: self._restore_ai_suggestions(after_suggestions),
            )
        self._status.showMessage(f"AI found {len(suggestions)} suggestion(s)", 6000)
        if project.project_path is not None:
            try:
                save_project(project, project.project_path)
                self._mark_project_saved()
            except OSError as exc:
                logger.warning("Could not save project after AI detection: %s", exc)

    def _on_ai_job_failed(self, message: str) -> None:
        if self._release_ai_job() is None:
            return
        QMessageBox.warning(self, "AI detection failed", message)

    def _on_ai_job_cancelled(self) -> None:
        if self._release_ai_job() is None:
            return
        self._status.showMessage("AI detection cancelled", 4000)

    def _on_ai_job_busy(self, busy: bool) -> None:
        self._suggestions_panel.set_busy(busy)
        if not busy:
            self._suggestions_panel.hide_job_banner()
            self._release_prep_after_user_job()
