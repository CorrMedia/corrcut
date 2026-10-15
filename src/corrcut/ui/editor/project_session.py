"""Project open/save/close, relink, and dirty-state tracking."""

from __future__ import annotations

import json
import logging
import sys
import time
from collections.abc import Callable
from pathlib import Path

from PySide6.QtCore import QEventLoop, QTimer
from PySide6.QtWidgets import QApplication, QFileDialog, QMessageBox

from corrcut.config import APP_NAME
from corrcut.domain.corr_sidecar import (
    load_corr_file,
    media_path_for_sidecar,
    operations_from_corr_document,
    operations_to_corr_document,
    save_corr_file,
    sidecar_path,
)
from corrcut.domain.edl import load_edl_file, media_path_for_edl
from corrcut.domain.project import Project
from corrcut.domain.relink import (
    apply_relink,
    fingerprint_matches,
    missing_track_stream_indices,
)
from corrcut.domain.skp import load_skp_file, media_path_for_skp
from corrcut.engine.jobs.job_runner import JobRunner
from corrcut.io.file_drop import classify_open_path
from corrcut.io.media_open import PreparedMediaOpen, prepare_media_open
from corrcut.io.probe_cache import probe_media_cached
from corrcut.io.project_store import (
    PROJECT_SUFFIX,
    default_project_path_for_source,
    load_project,
    save_project,
)
from corrcut.io.recent_posters import discard_recent_poster, schedule_recent_poster
from corrcut.io.recent_projects import add_recent_project, remove_recent_project
from corrcut.ui.closing_notice import ClosingNotice, closing_wait_detail
from corrcut.ui.export_progress_dialog import ExportProgressDialog
from corrcut.ui.theme import choose_open_path, choose_save_path

logger = logging.getLogger(__name__)


class EditorProjectMixin:
    _media_open_dialog: ExportProgressDialog | None
    _closing_application: bool
    _quit_in_progress: bool
    _quit_accepted: bool
    _closing_notice: ClosingNotice | None
    _quit_deadline_monotonic: float

    def _project_snapshot(self, project: Project) -> str:
        return json.dumps(project.to_json(), sort_keys=True)

    def _establish_save_baseline(self) -> None:
        if self._project is None:
            self._saved_project_snapshot = None
            self._project_dirty = False
            return
        self._saved_project_snapshot = self._project_snapshot(self._project)
        self._project_dirty = False

    def _mark_project_saved(self) -> None:
        self._establish_save_baseline()
        self._update_window_title()

    def _reconcile_project_dirty(self) -> bool:
        """Compare project state to the last save baseline (accurate but not free)."""
        if self._project is None:
            self._project_dirty = False
            return False
        current = self._project_snapshot(self._project)
        if self._saved_project_snapshot is None:
            dirty = bool(
                self._project.operations or self._project.exports or self._project.ai_suggestions
            )
        else:
            dirty = current != self._saved_project_snapshot
        self._project_dirty = dirty
        return dirty

    def _project_window_title_base(self) -> str:
        if self._project is None:
            return APP_NAME
        if self._project.project_path is not None:
            name = self._project.project_path.name
        elif self._project.source is not None:
            name = self._project.source.path.name
        else:
            name = "untitled"
        return f"{APP_NAME} — {name}"

    def _update_window_title(self) -> None:
        if self._closing_application:
            self.setWindowTitle(f"{APP_NAME} — Closing…")
            return
        base = self._project_window_title_base()
        if self._project is not None and self._project_dirty:
            self.setWindowTitle(f"{base} *")
        else:
            self.setWindowTitle(base)

    def _notify_project_modified(self) -> None:
        if self._project_dirty:
            return
        self._project_dirty = True
        self._update_window_title()

    def _sync_dirty_after_history_step(self) -> None:
        """Reconcile dirty state after undo/redo (may clear the unsaved marker)."""
        was_dirty = self._project_dirty
        self._reconcile_project_dirty()
        if self._project_dirty != was_dirty:
            self._update_window_title()

    def _report_project_saved(self, path: Path) -> None:
        self._mark_project_saved()
        self._status.showMessage(f"Saved {path.name}", 5000)

    def _is_project_dirty(self) -> bool:
        if self._project is None or not self._project_dirty:
            return False
        return self._reconcile_project_dirty()

    def _project_display_name(self) -> str:
        if self._project is None:
            return "Project"
        if self._project.project_path is not None:
            return self._project.project_path.name
        if self._project.source is not None:
            return self._project.source.path.name
        return "Untitled"

    def _confirm_discard_unsaved_changes(self, *, action: str) -> bool:
        if not self._is_project_dirty():
            return True
        reply = QMessageBox.warning(
            self,
            "Unsaved changes",
            f'"{self._project_display_name()}" has unsaved changes.\n\n'
            f"Save your changes before you {action}?",
            QMessageBox.StandardButton.Save
            | QMessageBox.StandardButton.Discard
            | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Save,
        )
        if reply == QMessageBox.StandardButton.Cancel:
            return False
        if reply == QMessageBox.StandardButton.Save:
            self._save_corr_json()
            return not self._is_project_dirty()
        return True

    def _sync_relink_action(self) -> None:
        enabled = self._project is not None and self._project.source is not None
        self._relink_action.setEnabled(enabled)

    def _win32_file_dialog_options(self) -> QFileDialog.Option:
        return QFileDialog.Option.DontUseNativeDialog

    def _pick_open_path(
        self,
        title: str,
        file_filter: str,
        *,
        initial_dir: str = "",
    ) -> Path | None:
        self._preview_panel.prepare_for_modal_dialog()
        if sys.platform == "win32":
            QApplication.processEvents(QEventLoop.ProcessEventsFlag.ExcludeUserInputEvents)
        dialog_options = QFileDialog.Option(0)
        if sys.platform == "win32":
            dialog_options = self._win32_file_dialog_options()
        try:
            return choose_open_path(
                self,
                title,
                file_filter,
                initial_dir=initial_dir,
                options=dialog_options,
            )
        finally:
            self._preview_panel.restore_after_modal_dialog()

    def _suggested_project_save_path(self) -> str:
        if self._project is None or self._project.source is None:
            return ""
        return str(default_project_path_for_source(self._project.source.path))

    def _pick_save_path(
        self,
        title: str,
        file_filter: str,
        *,
        initial: str = "",
    ) -> Path | None:
        self._preview_panel.prepare_for_modal_dialog()
        if sys.platform == "win32":
            QApplication.processEvents(QEventLoop.ProcessEventsFlag.ExcludeUserInputEvents)
        dialog_options = (
            self._win32_file_dialog_options() if sys.platform == "win32" else QFileDialog.Option(0)
        )
        try:
            return choose_save_path(
                self,
                title,
                file_filter,
                initial=initial,
                options=dialog_options,
            )
        finally:
            self._preview_panel.restore_after_modal_dialog()

    def _defer_ok(self, token: int) -> bool:
        return token == self._defer_token

    def _schedule_deferred(self, delay_ms: int, callback) -> None:  # type: ignore[no-untyped-def]
        token = self._defer_token

        def _run() -> None:
            if not self._defer_ok(token):
                return
            callback()

        QTimer.singleShot(delay_ms, _run)

    def _can_open_dropped_file(self) -> bool:
        """True while the editor has no project and no media to replace."""
        return self._project is None or self._project.source is None

    def _open_dropped_file(self, path: Path) -> None:
        if not self._can_open_dropped_file():
            return
        kind = classify_open_path(path)
        if kind is None:
            return
        if not path.is_file():
            self._status.showMessage(f"File not found: {path.name}", 4000)
            return
        if kind == "project":
            self.load_project_file(path)
            return
        if self._project is not None:
            self._prepare_replace_session()
        self._begin_media_open(path)

    def _open_media(self) -> None:
        self._schedule_deferred(0, self._open_media_when_idle)

    def _any_background_job_busy(self) -> bool:
        return (
            self._job_runner.is_busy
            or self._media_open_job_runner.is_busy
            or self._ai_job_runner.is_busy
            or self._subtitle_job_runner.is_busy
            or self._waveform_job_runner.is_busy
            or self._scene_cut_job_runner.is_busy
        )

    def _prepare_replace_session(self) -> None:
        self._prep_held_for_user_job = False
        self._scene_cuts_resume_after_user_job = False
        self._defer_token += 1
        self._waveform_generation += 1
        self._peak_preload_token += 1
        self._waveform_gate_open_for_scene_cuts = False
        self._scene_cuts_waiting_for_waveform = False
        self._invalidate_embedded_subtitle_extract()
        self._preview_panel.cancel_pending_work()
        for runner in (
            self._waveform_job_runner,
            self._ai_job_runner,
            self._subtitle_job_runner,
            self._job_runner,
        ):
            runner.force_stop(wait_ms=10_000)
        self._filmstrip_generation += 1
        self._filmstrip_pending = []
        self._filmstrip_primary_count = 0
        self._filmstrip_in_flight = []
        self._filmstrip_job_runner.force_stop(wait_ms=10_000)
        self._scene_cut_generation += 1
        self._scene_cut_job_runner.force_stop(wait_ms=10_000)
        self._subtitle_extract_job_runner.force_stop(wait_ms=10_000)

    def _open_media_when_idle(self, _attempt: int = 0) -> None:
        if self._any_background_job_busy():
            if _attempt < 150:
                self._schedule_deferred(
                    100,
                    lambda: self._open_media_when_idle(_attempt + 1),
                )
                return
            self._prepare_replace_session()
            if self._any_background_job_busy():
                self._status.showMessage("Still waiting for background job to stop…", 3000)
                self._schedule_deferred(
                    200,
                    lambda: self._open_media_when_idle(_attempt + 1),
                )
                return
        self._open_media_dialog()

    def _refresh_project_probe(self, project: Project) -> None:
        if project.source is None or not project.source.path.is_file():
            return
        try:
            probe, fp, _cache_hit = probe_media_cached(
                project.source.path,
                self._paths.cache_dir,
            )
            project.source.fingerprint = fp
            project.refresh_probe_metadata(probe)
        except Exception as exc:
            logger.warning("Could not refresh probe for %s: %s", project.source.path, exc)

    def _open_media_dialog(self) -> None:
        path = self._pick_open_path(
            "Open media",
            "Media (*.mkv *.mp4 *.avi *.mov *.m4v *.wav *.flac);;All (*.*)",
        )
        if path is None:
            return
        if self._project is not None:
            self._prepare_replace_session()
        self._begin_media_open(path)

    def start_new_project(self, *, prompt_open_media: bool = False) -> None:
        if self._project is not None:
            self._teardown_active_project()
        self._set_project(Project())
        if prompt_open_media:
            self._schedule_deferred(0, self._open_media_when_idle)

    def load_project_file(self, path: Path) -> bool:
        if self._project is not None and not self._confirm_discard_unsaved_changes(
            action="open another project"
        ):
            return False
        if self._project is not None:
            self._prepare_replace_session()
        dialog = self._begin_work_dialog(f"Opening {path.name}", "Open project", "Reading project…")
        try:
            self._paint_work_dialog(dialog, 15, "Reading project…")
            project = load_project(path)
            missing = project.source is not None and not project.source.path.is_file()
            if not missing and project.source is not None:
                self._paint_work_dialog(dialog, 40, "Checking media…")
                self._refresh_project_probe(project)
            self._paint_work_dialog(dialog, 70, "Building the timeline…")
            self._project = project
            self._set_project(self._project, defer_ui=False)
            self._fill_project_panels(dialog, subtitles_ready=False)
            self._remember_recent_project(path)
            if not missing:
                self._schedule_deferred(0, self._ensure_waveform)
            self._close_work_dialog(dialog)
        except Exception as exc:
            self._close_work_dialog(dialog)
            QMessageBox.critical(self, "Load failed", str(exc))
            return False
        if missing and self._project is not None and self._project.source is not None:
            reply = QMessageBox.warning(
                self,
                "Missing media",
                f"Source file not found:\n{self._project.source.path}\n\n"
                "Choose a replacement file now?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.Yes,
            )
            if reply == QMessageBox.StandardButton.Yes:
                self._relink_media()
        return True

    def _exit_application(self) -> None:
        self.close()

    def _new_project(self) -> None:
        self.start_new_project()

    def _teardown_active_project(self) -> None:
        self._prepare_replace_session()
        self._set_project(None)

    def _close_project(self) -> None:
        if self._project is None:
            return
        if not self._confirm_discard_unsaved_changes(action="close the project"):
            return
        self._teardown_active_project()
        self._status.showMessage("Project closed", 3000)

    def _quit_job_runners(self) -> tuple[JobRunner, ...]:
        return (
            self._waveform_job_runner,
            self._ai_job_runner,
            self._job_runner,
            self._media_open_job_runner,
            self._filmstrip_job_runner,
            self._scene_cut_job_runner,
            self._subtitle_extract_job_runner,
            self._subtitle_job_runner,
        )

    def _quit_runners_still_active(self) -> list[JobRunner]:
        return [
            runner
            for runner in self._quit_job_runners()
            if runner.is_busy or runner.is_thread_running
        ]

    def _cancel_quit_workers(self) -> None:
        """Ask every quit-time worker to stop without blocking the UI thread."""
        self._waveform_generation += 1
        self._peak_preload_token += 1
        self._filmstrip_generation += 1
        self._filmstrip_pending = []
        self._filmstrip_primary_count = 0
        self._filmstrip_in_flight = []
        self._scene_cut_generation += 1
        self._invalidate_embedded_subtitle_extract()
        self._preview_panel.cancel_pending_work()
        for runner in self._quit_job_runners():
            runner.request_stop()

    def _show_closing_notice(self, labels: list[str]) -> ClosingNotice:
        self._closing_application = True
        self._update_window_title()
        self._status.showMessage("Closing…")
        notice = ClosingNotice(self)
        notice.set_detail(closing_wait_detail(labels))
        notice.paint_now()
        QApplication.processEvents(QEventLoop.ProcessEventsFlag.ExcludeUserInputEvents)
        self._status.showMessage("Closing…")
        return notice

    def _dismiss_closing_notice(self) -> None:
        notice = self._closing_notice
        self._closing_notice = None
        if notice is None:
            return
        notice.hide()
        notice.deleteLater()

    def _finish_application_quit(self) -> None:
        self._dismiss_closing_notice()
        self._quit_accepted = True
        self.close()

    def _poll_quit_workers(self) -> None:
        if self._quit_accepted:
            return
        active = self._quit_runners_still_active()
        notice = self._closing_notice
        if notice is not None:
            notice.set_detail(closing_wait_detail([runner.active_label for runner in active]))
            notice.repaint()
        if active and time.monotonic() < self._quit_deadline_monotonic:
            QTimer.singleShot(100, self._poll_quit_workers)
            return
        for runner in active:
            runner.force_stop(wait_ms=2_000, pump_events=True)
        self._finish_application_quit()

    def _begin_async_quit(self) -> None:
        active = self._quit_runners_still_active()
        labels = [runner.active_label for runner in active]
        self._closing_notice = self._show_closing_notice(labels)
        self._quit_deadline_monotonic = time.monotonic() + 12.0
        QTimer.singleShot(0, self._poll_quit_workers)

    def closeEvent(self, event) -> None:  # type: ignore[no-untyped-def]
        if self._quit_accepted:
            self._dismiss_closing_notice()
            super().closeEvent(event)
            app = QApplication.instance()
            if app is not None:
                app.quit()
            return
        if self._closing_application or self._quit_in_progress:
            event.ignore()
            return
        if not self._confirm_discard_unsaved_changes(action="quit"):
            event.ignore()
            return
        # Further project writes would race shutdown and can truncate the file.
        self._quit_in_progress = True
        self._defer_token += 1
        self._save_window_layout()
        if self._project is not None:
            # Soft teardown: cancel workers without blocking the UI thread.
            self._cancel_quit_workers()
            self._set_project(None)
        else:
            self._cancel_quit_workers()
        if self._quit_runners_still_active():
            event.ignore()
            self._begin_async_quit()
            return
        self._quit_accepted = True
        super().closeEvent(event)
        app = QApplication.instance()
        if app is not None:
            app.quit()

    def _open_recent_project(self, path: Path) -> None:
        self._schedule_deferred(0, lambda: self._open_recent_project_when_idle(path))

    def _open_recent_project_when_idle(self, path: Path, _attempt: int = 0) -> None:
        if self._any_background_job_busy():
            if _attempt < 150:
                self._schedule_deferred(
                    100,
                    lambda: self._open_recent_project_when_idle(path, _attempt + 1),
                )
                return
            self._prepare_replace_session()
            if self._any_background_job_busy():
                self._status.showMessage("Still waiting for background job to stop…", 3000)
                self._schedule_deferred(
                    200,
                    lambda: self._open_recent_project_when_idle(path, _attempt + 1),
                )
                return
        self._open_recent_project_now(path)

    def _open_recent_project_now(self, path: Path) -> None:
        if not path.is_file():
            reply = QMessageBox.warning(
                self,
                "Project not found",
                f"The project file no longer exists:\n{path}\n\n" "Remove it from recent projects?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.Yes,
            )
            if reply == QMessageBox.StandardButton.Yes:
                remove_recent_project(path)
            return
        self.load_project_file(path)

    def _open_project(self) -> None:
        self._schedule_deferred(0, self._open_project_when_idle)

    def _open_project_when_idle(self, _attempt: int = 0) -> None:
        if self._any_background_job_busy():
            if _attempt < 150:
                self._schedule_deferred(
                    100,
                    lambda: self._open_project_when_idle(_attempt + 1),
                )
                return
            self._prepare_replace_session()
            if self._any_background_job_busy():
                self._status.showMessage("Still waiting for background job to stop…", 3000)
                self._schedule_deferred(
                    200,
                    lambda: self._open_project_when_idle(_attempt + 1),
                )
                return
        self._open_project_dialog()

    def _open_project_dialog(self) -> None:
        path = self._pick_open_path(
            "Open project",
            f"{APP_NAME} project (*{PROJECT_SUFFIX});;All (*.*)",
        )
        if path is None:
            return
        self.load_project_file(path)

    def _relink_media(self) -> None:
        if self._project is None or self._project.source is None:
            return
        old_path = self._project.source.path
        stored_fp = self._project.source.fingerprint
        initial_dir = ""
        if old_path.parent.is_dir():
            initial_dir = str(old_path.parent)
        media_filter = "Media (*.mkv *.mp4 *.avi *.mov *.m4v *.wav *.flac);;All (*.*)"
        new_path = self._pick_open_path(
            "Relink media",
            media_filter,
            initial_dir=initial_dir,
        )
        if new_path is None:
            return
        try:
            probe, fp, _cache_hit = probe_media_cached(
                new_path,
                self._paths.cache_dir,
            )
        except Exception as exc:
            QMessageBox.critical(self, "Relink failed", str(exc))
            return
        if not fingerprint_matches(stored_fp, fp):
            reply = QMessageBox.warning(
                self,
                "Different file",
                f"The selected file does not match the original media fingerprint.\n\n"
                f"Original: {old_path.name}\n"
                f"Selected: {new_path.name}\n\n"
                "Timeline edits may not align. Relink anyway?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if reply != QMessageBox.StandardButton.Yes:
                return
        missing_streams = missing_track_stream_indices(self._project, probe)
        if missing_streams:
            labels = ", ".join(f"#{idx}" for idx in missing_streams)
            reply = QMessageBox.warning(
                self,
                "Missing audio streams",
                f"The replacement file is missing project audio stream(s): {labels}\n\n"
                "Edits on those tracks will not work. Continue?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if reply != QMessageBox.StandardButton.Yes:
                return
        self._prepare_replace_session()
        try:
            apply_relink(self._project, new_path, probe, fp)
        except ValueError as exc:
            QMessageBox.critical(self, "Relink failed", str(exc))
            return
        self._set_project(self._project)
        self._schedule_recent_poster(refresh=True)
        self._schedule_deferred(0, self._ensure_waveform)
        self._status.showMessage(f"Relinked media: {new_path.name}", 5000)

    def _save_project(self) -> None:
        if self._quit_in_progress or self._project is None:
            return
        if self._project.project_path is None:
            self._save_project_as()
            return
        path = self._project.project_path
        try:
            save_project(self._project, path)
        except OSError as exc:
            QMessageBox.critical(self, "Save failed", str(exc))
            return
        self._remember_recent_project(path)
        self._report_project_saved(path)

    def _save_project_as(self) -> None:
        if self._quit_in_progress or self._project is None:
            return
        p = self._pick_save_path(
            "Save project",
            f"{APP_NAME} project (*{PROJECT_SUFFIX})",
            initial=self._suggested_project_save_path(),
        )
        if p is None:
            return
        if p.suffix != PROJECT_SUFFIX:
            p = p.with_suffix(PROJECT_SUFFIX)
        try:
            save_project(self._project, p)
        except OSError as exc:
            QMessageBox.critical(self, "Save failed", str(exc))
            return
        self._remember_recent_project(p)
        self._report_project_saved(p)

    def _remember_recent_project(self, path: Path) -> None:
        add_recent_project(path)
        self._schedule_recent_poster()

    def _schedule_recent_poster(self, *, refresh: bool = False) -> None:
        project = self._project
        if project is None or project.project_path is None:
            return
        if refresh:
            discard_recent_poster(self._paths.cache_dir, project.project_path)
        schedule_recent_poster(self._paths.cache_dir, project)

    def _default_corr_path(self) -> Path | None:
        if self._project is None or self._project.source is None:
            return None
        existing = getattr(self, "_corr_path", None)
        if isinstance(existing, Path):
            return existing
        return sidecar_path(self._project.source.path)

    def _begin_media_open(self, path: Path) -> None:
        dialog = ExportProgressDialog(
            f"Opening {path.name}",
            window_title="Open media",
            status="Probing media…",
            log_placeholder="Open log…",
            parent=self,
        )
        self._media_open_dialog = dialog
        dialog.cancel_requested.connect(self._media_open_job_runner.cancel_active)
        dialog.show()

        cache_dir = self._paths.cache_dir
        cancel = self._media_open_job_runner.cancel_event
        runner = self._media_open_job_runner

        def _progress(percent: int, message: str) -> None:
            runner.job_progress.emit(percent, message)

        def _prepare() -> PreparedMediaOpen:
            return prepare_media_open(
                path,
                cache_dir,
                cancel_event=cancel,
                progress=_progress,
            )

        if not runner.run_callable(f"Open {path.name}", _prepare):
            self._finish_media_open_dialog()
            self._status.showMessage("Could not start opening media", 4000)
            self._resume_open_project_background_work()

    def _finish_media_open_dialog(self) -> None:
        dialog = self._media_open_dialog
        self._media_open_dialog = None
        if dialog is None:
            return
        self._close_work_dialog(dialog)

    def _begin_work_dialog(
        self,
        heading: str,
        window_title: str,
        status: str,
    ) -> ExportProgressDialog:
        dialog = ExportProgressDialog(
            heading,
            window_title=window_title,
            status=status,
            log_placeholder="Open log…",
            parent=self,
        )
        dialog.set_cancel_enabled(False)
        dialog.show()
        self._paint_work_dialog(dialog, 5, status)
        return dialog

    def _paint_work_dialog(self, dialog: ExportProgressDialog, percent: int, message: str) -> None:
        dialog.set_progress(percent, message)
        QApplication.processEvents(QEventLoop.ProcessEventsFlag.ExcludeUserInputEvents)

    def _close_work_dialog(self, dialog: ExportProgressDialog) -> None:
        dialog.mark_finished()
        dialog.accept()
        dialog.deleteLater()

    def _fill_project_panels(
        self,
        dialog: ExportProgressDialog,
        *,
        subtitles_ready: bool,
    ) -> None:
        """Build the edits list and subtitle table before the progress dialog closes."""
        self._paint_work_dialog(dialog, 88, "Loading edits…")
        self._refresh_operations_panel(sync_timeline=False, sync_preview=False)
        self._paint_work_dialog(dialog, 94, "Loading subtitles…")
        if subtitles_ready:
            self._present_loaded_subtitles()
        else:
            self._load_subtitles_on_idle()
        self._paint_work_dialog(dialog, 98, "Checking the picture…")
        self._ensure_video_matte_and_refresh()

    def _on_media_open_log(self, message: str) -> None:
        dialog = self._media_open_dialog
        if dialog is not None:
            dialog.append_log(message)

    def _on_media_open_progress(self, percent: int, message: str) -> None:
        dialog = self._media_open_dialog
        if dialog is not None:
            dialog.set_progress(percent, message)

    def _on_media_open_result(self, result: object) -> None:
        if not isinstance(result, PreparedMediaOpen):
            self._finish_media_open_dialog()
            return
        self._apply_prepared_media(result)
        self._finish_media_open_dialog()

    def _on_media_open_failed(self, message: str) -> None:
        self._finish_media_open_dialog()
        QMessageBox.critical(self, "Open failed", message)
        self._resume_open_project_background_work()

    def _on_media_open_cancelled(self) -> None:
        if self._media_open_dialog is None:
            return
        self._finish_media_open_dialog()
        self._status.showMessage("Open cancelled", 4000)
        self._resume_open_project_background_work()

    def _resume_open_project_background_work(self) -> None:
        if self._project is None or self._project.source is None:
            return
        self._schedule_deferred(0, self._ensure_waveform)
        self._schedule_deferred(0, self._ensure_filmstrip)
        self._schedule_deferred(0, self._ensure_scene_cuts)

    def _apply_prepared_media(self, prepared: PreparedMediaOpen) -> None:
        dialog = self._media_open_dialog
        self._corr_path = prepared.corr_path
        if dialog is not None:
            dialog.set_cancel_enabled(False)
            self._paint_work_dialog(dialog, 80, "Building the timeline…")
        self._set_project(
            prepared.project,
            subtitles_ready=False,
            defer_ui=dialog is None,
        )
        if dialog is not None:
            self._fill_project_panels(dialog, subtitles_ready=False)
        source = prepared.project.source
        name = source.path.name if source is not None else "media"
        hint = " (probe cache)" if prepared.probe_cache_hit else ""
        message = f"Loaded {name}{hint}"
        if prepared.sidecar_loaded:
            message += f" · sidecar {prepared.corr_path.name}"
        elif prepared.sidecar_error:
            logger.warning(
                "Could not auto-load corr sidecar %s: %s",
                prepared.corr_path,
                prepared.sidecar_error,
            )
            message += " · sidecar failed"
        self._status.showMessage(message, 5000)
        self._schedule_deferred(0, self._ensure_waveform)

    def _save_corr_json(self) -> None:
        if self._quit_in_progress:
            return
        if self._project is None:
            QMessageBox.information(self, "Export corr.json", "Open media before exporting.")
            return
        path = self._default_corr_path()
        if path is None:
            self._save_corr_json_as()
            return
        try:
            self._ensure_video_matte()
            document = operations_to_corr_document(self._project)
            save_corr_file(path, document)
        except OSError as exc:
            QMessageBox.critical(self, "Save failed", str(exc))
            return
        self._corr_path = path
        self._report_project_saved(path)

    def _save_corr_json_as(self) -> None:
        if self._quit_in_progress or self._project is None:
            return
        initial = str(self._default_corr_path() or "")
        path = self._pick_save_path(
            "Export corr.json",
            "Corr sidecar (*.corr.json);;JSON (*.json)",
            initial=initial,
        )
        if path is None:
            return
        if not path.name.endswith(".corr.json"):
            path = path.with_name(path.stem + ".corr.json")
        try:
            self._ensure_video_matte()
            document = operations_to_corr_document(self._project)
            save_corr_file(path, document)
        except OSError as exc:
            QMessageBox.critical(self, "Save failed", str(exc))
            return
        self._corr_path = path
        self._report_project_saved(path)

    def _open_corr_json(self) -> None:
        path = self._pick_open_path(
            "Open corr.json",
            "Corr sidecar (*.corr.json);;JSON (*.json);;All (*.*)",
        )
        if path is None:
            return
        try:
            data = load_corr_file(path)
        except Exception as exc:
            QMessageBox.critical(self, "Open failed", str(exc))
            return

        media = media_path_for_sidecar(path)
        if self._project is None or self._project.source is None:
            if media is None:
                QMessageBox.warning(
                    self,
                    "Open corr.json",
                    "Could not find a sibling media file for this sidecar. Open media first.",
                )
                return
            try:
                probe, fp, _ = probe_media_cached(media, self._paths.cache_dir)
                self._project = Project.from_probe(probe, fp)
                self._set_project(self._project)
            except Exception as exc:
                QMessageBox.critical(self, "Open failed", str(exc))
                return
        elif self._project.operations:
            reply = QMessageBox.question(
                self,
                "Replace edits?",
                "Replace the current edits with this corr.json?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.Yes,
            )
            if reply != QMessageBox.StandardButton.Yes:
                return

        stream = self._project.selected_audio_stream_index or 0
        self._project.operations = operations_from_corr_document(
            data,
            default_stream_index=stream,
            tracks=self._project.tracks,
        )
        self._project.select_track_for_audio_edits()
        self._corr_path = path
        self._refresh_operations_panel()
        self._notify_project_modified()
        self._status.showMessage(f"Loaded {path.name}", 5000)
        self._schedule_deferred(0, self._ensure_waveform)

    def _import_edl(self) -> None:
        self._import_edit_list(
            title="Import EDL",
            file_filter="EDL (*.edl);;All (*.*)",
            kind="EDL",
            load=load_edl_file,
            media_for=media_path_for_edl,
        )

    def _import_skp(self) -> None:
        self._import_edit_list(
            title="Import SKP",
            file_filter="VideoSkip (*.skp);;All (*.*)",
            kind="SKP",
            load=load_skp_file,
            media_for=media_path_for_skp,
        )

    def _import_edit_list(
        self,
        *,
        title: str,
        file_filter: str,
        kind: str,
        load: Callable[..., object],
        media_for: Callable[[Path], Path | None],
    ) -> None:
        initial = ""
        if self._project is not None and self._project.source is not None:
            initial = str(self._project.source.path.parent)
        path = self._pick_open_path(title, file_filter, initial_dir=initial)
        if path is None:
            return
        if self._project is None or self._project.source is None:
            media = media_for(path)
            if media is None:
                QMessageBox.warning(
                    self,
                    title,
                    f"Could not find a sibling media file for this {kind}. Open media first.",
                )
                return
            try:
                probe, fp, _ = probe_media_cached(media, self._paths.cache_dir)
                self._project = Project.from_probe(probe, fp)
                self._set_project(self._project)
            except Exception as exc:
                QMessageBox.critical(self, "Open failed", str(exc))
                return
        elif self._project.operations:
            reply = QMessageBox.question(
                self,
                "Replace edits?",
                f"Replace the current edits with this {kind}?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.Yes,
            )
            if reply != QMessageBox.StandardButton.Yes:
                return

        stream = self._project.selected_audio_stream_index or 0
        channels = self._project.default_channels_for_selected()
        try:
            result = load(
                path,
                default_stream_index=stream,
                default_channels=channels,
            )
        except OSError as exc:
            QMessageBox.critical(self, "Import failed", str(exc))
            return

        if not result.operations and not result.warnings:
            QMessageBox.information(self, title, "No edits were found in that file.")
            return
        if result.operations:
            self._project.operations = result.operations
            self._refresh_operations_panel()
            self._notify_project_modified()
            self._status.showMessage(
                f"Imported {len(result.operations)} edit(s) from {path.name}",
                5000,
            )
            self._schedule_deferred(0, self._ensure_waveform)
        self._show_edit_list_import_result(path, result, kind=kind)

    def _show_edit_list_import_result(self, path: Path, result, *, kind: str) -> None:
        imported = len(result.operations)
        title = f"Import {kind}"
        if not result.warnings:
            QMessageBox.information(
                self,
                title,
                f"Imported {imported} edit(s) from {path.name}.",
            )
            return
        lines = [warning.display() for warning in result.warnings]
        summary = (
            f"Imported {imported} edit(s) from {path.name}."
            if imported
            else f"No edits were imported from {path.name}."
        )
        summary += f"\n\n{len(result.warnings)} line(s) could not be mapped directly:"
        preview = "\n".join(f"- {line}" for line in lines[:12])
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Warning)
        box.setWindowTitle(f"{kind} import")
        box.setText(summary)
        box.setInformativeText(preview)
        if len(lines) > 12:
            box.setDetailedText("\n".join(lines))
        box.exec()
