"""Subtitle ingest, editing, and preview sync for the editor."""

from __future__ import annotations

import shutil

from PySide6.QtWidgets import QMessageBox

from corrcut.ai.transcribe import is_whisper_available, whisper_install_hint
from corrcut.ai.transcript import TranscriptWord
from corrcut.config import env_value
from corrcut.domain.project import Project
from corrcut.domain.subtitle_cue import SubtitleCue, SubtitleSource
from corrcut.engine.jobs.cancel import JobCancelled
from corrcut.engine.subtitles.bitmap_ocr import (
    convert_bitmap_stream_to_cues,
)
from corrcut.engine.subtitles.cue_edit import add_cue_at, delete_cue
from corrcut.engine.subtitles.cue_sets import (
    coerce_subtitle_source,
    ensure_active_source,
    source_label,
    switch_active_source,
)
from corrcut.engine.subtitles.ingest import (
    EmbeddedSubtitleExtract,
    apply_embedded_subtitle_cues,
    embedded_text_stream_label,
    has_bitmap_subtitle_streams,
    has_text_subtitle_streams,
    ingest_ocr_subtitles,
    ingest_whisper_subtitles,
    read_embedded_subtitle_cues,
)
from corrcut.engine.subtitles.ocr_refine import reapply_ocr_cleanup
from corrcut.engine.subtitles.paddle_ocr_engine import paddle_unavailable_reason
from corrcut.engine.subtitles.regenerate import regenerate_edited_texts
from corrcut.engine.subtitles.service import on_operations_changed, store_whisper_cache
from corrcut.engine.subtitles.whisper_transcribe import transcribe_project_track
from corrcut.engine.toolchain import check_toolchain


class EditorSubtitlesMixin:
    _subtitle_job_kind: str | None
    _subtitle_job_project: Project | None
    def _refresh_subtitle_ui(self) -> None:
        if self._project is None:
            return
        self._subtitles_panel.load_project(self._project)
        self._preview_panel.sync_subtitle_overlay()

    def _ingest_subtitles_on_load(self) -> None:
        if self._project is None or self._project.source is None:
            return
        if self._project.subtitle_cue_sets:
            ensure_active_source(self._project)
            if self._project.whisper_cache and not self._project.has_whisper_subtitles():
                ingest_whisper_subtitles(self._project)
            regenerate_edited_texts(self._project)
            return
        self._begin_embedded_subtitle_extract()

    def _invalidate_embedded_subtitle_extract(self) -> None:
        """Drop an in-flight extract so it cannot land on a different project."""
        self._extract_cancel_generation = self._embedded_extract_job_generation
        self._subtitle_extract_generation += 1
        self._embedded_extract_pending = False
        self._embedded_extract_show_cancel = False
        if self._subtitle_extract_job_runner.is_busy:
            self._subtitle_extract_job_runner.cancel_active()

    def _cancel_embedded_subtitle_extract(self) -> None:
        if not self._embedded_extract_active and not self._subtitle_extract_job_runner.is_busy:
            return
        self._extract_cancel_generation = self._embedded_extract_job_generation
        self._embedded_extract_pending = False
        self._embedded_extract_show_cancel = True
        self._subtitle_extract_generation += 1
        self._subtitle_extract_job_runner.cancel_active()

    def _show_embedded_extract_status(self) -> None:
        project = self._project
        label = embedded_text_stream_label(project) if project is not None else None
        if label:
            self._subtitles_panel.set_extract_status(f"Loading embedded subtitles: {label}…")
        else:
            self._subtitles_panel.set_extract_status("Loading embedded subtitles…")
        self._status.showMessage("Extracting subtitles…", 0)

    def _begin_embedded_subtitle_extract(self, *, force: bool = False) -> None:
        """Start a background text-track extract, or restart one already running."""
        project = self._project
        if project is None or project.source is None or not project.source.path.is_file():
            return
        probe = project.source.probe or {}
        if not has_text_subtitle_streams(probe):
            if not project.subtitle_cue_sets and ingest_whisper_subtitles(project):
                self._present_loaded_subtitles()
                self._note_auto_subtitles_loaded()
            return
        embedded = project.subtitle_cue_sets.get(SubtitleSource.EMBEDDED.value) or []
        if embedded and not force:
            return
        self._embedded_extract_from_user = force
        if self._subtitle_extract_job_runner.is_busy:
            self._extract_cancel_generation = self._embedded_extract_job_generation
            self._embedded_extract_pending = True
            self._embedded_extract_show_cancel = False
            self._show_embedded_extract_status()
            self._subtitle_extract_job_runner.cancel_active()
            return
        self._start_embedded_subtitle_extract()

    def _start_embedded_subtitle_extract(self) -> None:
        project = self._project
        if project is None or project.source is None:
            return
        self._subtitle_extract_generation += 1
        generation = self._subtitle_extract_generation
        self._embedded_extract_job_generation = generation
        cache_dir = self._paths.cache_dir
        cancel = self._subtitle_extract_job_runner.cancel_event
        self._embedded_extract_active = True
        self._show_embedded_extract_status()

        def _run() -> EmbeddedSubtitleExtract:
            try:
                found = read_embedded_subtitle_cues(
                    project,
                    cache_dir=cache_dir,
                    cancel_event=cancel,
                )
            except JobCancelled:
                raise
            except Exception as exc:
                return EmbeddedSubtitleExtract(
                    generation=generation,
                    stream_index=-1,
                    cues=(),
                    error=str(exc),
                )
            if found is None:
                return EmbeddedSubtitleExtract(
                    generation=generation,
                    stream_index=-1,
                    cues=(),
                )
            stream_index, cues = found
            return EmbeddedSubtitleExtract(
                generation=generation,
                stream_index=stream_index,
                cues=tuple(cues),
            )

        if not self._subtitle_extract_job_runner.run_callable("Extract subtitles", _run):
            self._embedded_extract_active = False
            self._subtitles_panel.set_extract_status(None)

    def _note_auto_subtitles_loaded(self) -> None:
        """Keep an untouched open from prompting to save cues the user did not edit."""
        if self._project_dirty:
            self._notify_project_modified()
        else:
            self._establish_save_baseline()

    def _finish_superseded_extract(self) -> None:
        if self._embedded_extract_active:
            return
        if self._embedded_extract_pending and self._project is not None:
            self._embedded_extract_pending = False
            self._start_embedded_subtitle_extract()
            return
        self._subtitles_panel.set_extract_status(None)
        if self._embedded_extract_show_cancel and self._project is not None:
            self._status.showMessage("Subtitle extract cancelled", 4000)
        self._embedded_extract_show_cancel = False

    def _on_embedded_extract_result(self, result: object) -> None:
        if not isinstance(result, EmbeddedSubtitleExtract):
            return
        if result.generation != self._embedded_extract_job_generation:
            return
        self._embedded_extract_active = False
        if self._embedded_extract_pending and self._project is not None:
            self._embedded_extract_pending = False
            self._start_embedded_subtitle_extract()
            return
        if (
            self._project is None
            or result.generation != self._subtitle_extract_generation
        ):
            self._finish_superseded_extract()
            return
        if result.error:
            self._subtitles_panel.set_extract_status(None)
            self._status.showMessage(f"Subtitles unavailable: {result.error}", 8000)
            return
        preferred = self._project.subtitle_settings.stream_index
        if preferred is not None and result.stream_index >= 0 and preferred != result.stream_index:
            self._subtitles_panel.set_extract_status(None)
            return
        if not result.cues:
            self._subtitles_panel.set_extract_status(None)
            if ingest_whisper_subtitles(self._project):
                self._present_loaded_subtitles()
                self._note_auto_subtitles_loaded()
                self._status.showMessage("Loaded whisper subtitles", 5000)
                return
            self._status.showMessage("No embedded subtitle cues in this file", 5000)
            return
        if not apply_embedded_subtitle_cues(
            self._project,
            result.stream_index,
            list(result.cues),
        ):
            self._subtitles_panel.set_extract_status(None)
            return
        self._subtitles_panel.set_extract_status(None)
        self._present_loaded_subtitles()
        if self._embedded_extract_from_user:
            self._notify_project_modified()
        else:
            self._note_auto_subtitles_loaded()
        self._status.showMessage("Loaded embedded subtitles", 5000)

    def _on_embedded_extract_failed(self, message: str) -> None:
        if self._embedded_extract_job_generation != self._subtitle_extract_generation:
            self._embedded_extract_active = False
            self._finish_superseded_extract()
            return
        self._embedded_extract_active = False
        if self._embedded_extract_pending and self._project is not None:
            self._embedded_extract_pending = False
            self._start_embedded_subtitle_extract()
            return
        self._subtitles_panel.set_extract_status(None)
        if self._embedded_extract_show_cancel:
            self._embedded_extract_show_cancel = False
            if self._project is not None:
                self._status.showMessage("Subtitle extract cancelled", 4000)
            return
        if self._project is None:
            return
        self._status.showMessage(f"Subtitles unavailable: {message}", 8000)

    def _on_embedded_extract_cancelled(self) -> None:
        if (
            self._embedded_extract_active
            and self._embedded_extract_job_generation != self._extract_cancel_generation
        ):
            return
        self._embedded_extract_active = False
        if self._embedded_extract_pending and self._project is not None:
            self._embedded_extract_pending = False
            self._start_embedded_subtitle_extract()
            return
        self._subtitles_panel.set_extract_status(None)
        if self._embedded_extract_show_cancel and self._project is not None:
            self._status.showMessage("Subtitle extract cancelled", 4000)
        self._embedded_extract_show_cancel = False

    def _on_cue_source_selected(self, source: object) -> None:
        if self._project is None:
            return
        resolved = coerce_subtitle_source(source)
        if resolved is None:
            return
        if not switch_active_source(self._project, resolved):
            return
        self._preview_panel.sync_subtitle_overlay()
        self._notify_project_modified()
        self._status.showMessage(f"Using {source_label(resolved)} subtitle cues", 5000)

    def _on_subtitle_settings_changed(self, settings: object) -> None:
        from corrcut.domain.subtitle_settings import SubtitleSettings

        if self._project is None or not isinstance(settings, SubtitleSettings):
            return
        previous_stream = self._project.subtitle_settings.stream_index
        self._project.subtitle_settings = settings
        if settings.stream_index != previous_stream and has_text_subtitle_streams(
            self._project.source.probe if self._project.source else {}
        ):
            self._begin_embedded_subtitle_extract(force=True)
        self._refresh_subtitle_ui()
        self._notify_project_modified()

    def _on_subtitles_refresh_requested(self) -> None:
        if self._project is None:
            return
        probe = self._project.source.probe if self._project.source else {}
        if has_text_subtitle_streams(probe):
            self._begin_embedded_subtitle_extract(force=True)
            return
        if ingest_whisper_subtitles(self._project):
            self._refresh_subtitle_ui()
            self._notify_project_modified()
            self._status.showMessage("Loaded whisper subtitles", 5000)
            return
        if has_bitmap_subtitle_streams(probe):
            self._offer_bitmap_subtitle_conversion(
                "No embedded text subtitles or cached Whisper transcript are available.\n\n"
                "Convert a bitmap subtitle track to text with OCR?\n"
                "This may take several minutes and requires PaddleOCR (see install-ocr.ps1)."
            )
            return
        self._offer_whisper_subtitle_generation(
            "No embedded text subtitles or cached Whisper transcript are available.\n\n"
            "Generate subtitles by transcribing the selected audio track with Whisper?\n"
            "This may take several minutes."
        )

    def _on_subtitles_generate_requested(self) -> None:
        if self._project is None:
            return
        if self._project.whisper_cache is not None:
            if not ingest_whisper_subtitles(self._project):
                QMessageBox.information(self, "Subtitles", "Could not build cues from Whisper.")
                return
            self._refresh_subtitle_ui()
            self._notify_project_modified()
            self._status.showMessage("Generated subtitles from Whisper cache", 5000)
            return
        self._offer_whisper_subtitle_generation(
            "No cached Whisper transcript yet.\n\n"
            "Transcribe the selected audio track with Whisper to generate subtitles?\n"
            "This may take several minutes."
        )

    def _on_convert_bitmap_requested(self) -> None:
        if self._project is None:
            return
        self._offer_bitmap_subtitle_conversion(
            "Convert a bitmap subtitle track to editable text cues with OCR?\n\n"
            "This may take several minutes."
        )

    def _offer_bitmap_subtitle_conversion(self, intro: str) -> None:
        request = self._subtitles_panel.prompt_bitmap_conversion(intro=intro)
        if request is None or self._project is None:
            return
        previous = self._project.subtitle_settings.bitmap_stream_index
        self._subtitles_panel.remember_bitmap_stream_index(request.stream_index)
        if previous != request.stream_index:
            self._notify_project_modified()
        self._start_bitmap_subtitle_conversion()

    def _bitmap_subtitle_prerequisites_ok(self) -> bool:
        if self._project is None or self._project.source is None:
            QMessageBox.information(self, "Subtitles", "Open media before converting subtitles.")
            return False
        if not self._project.source.path.is_file():
            QMessageBox.information(
                self,
                "Subtitles",
                "Media file is missing. Use File → Relink media…",
            )
            return False
        probe = self._project.source.probe or {}
        if not has_bitmap_subtitle_streams(probe):
            QMessageBox.information(
                self,
                "Subtitles",
                "No bitmap subtitle stream is available in this file.",
            )
            return False
        if self._subtitle_job_runner.is_busy:
            QMessageBox.information(self, "Subtitles", "Wait for the current subtitle job to finish.")
            return False
        if self._ai_job_runner.is_busy:
            QMessageBox.information(self, "Subtitles", "Wait for the current AI job to finish.")
            return False
        reason = paddle_unavailable_reason()
        if reason:
            QMessageBox.warning(
                self,
                "OCR not available",
                reason,
            )
            return False
        ffmpeg_ok = any(t.name == "ffmpeg" and t.ok for t in check_toolchain().tools)
        if not ffmpeg_ok:
            QMessageBox.warning(self, "Subtitles", "ffmpeg is required to extract PGS subtitles.")
            return False
        probe = self._project.source.probe or {}
        subtitle_streams = [
            s for s in probe.get("subtitle_streams", []) if isinstance(s, dict)
        ]
        settings = self._subtitles_panel.subtitle_settings()
        track = self._project.selected_track()
        from corrcut.ai.subtitles import (
            is_dvd_subtitle_codec,
            resolve_mkvextract,
            select_bitmap_subtitle_stream_index,
        )

        stream_index = select_bitmap_subtitle_stream_index(
            subtitle_streams,
            preferred_index=settings.bitmap_stream_index,
            audio_language=track.language if track else None,
            ocr_only=True,
        )
        if stream_index is not None:
            stream = next(
                (s for s in subtitle_streams if int(s.get("index", -1)) == stream_index),
                None,
            )
            codec = str(stream.get("codec", "")) if isinstance(stream, dict) else ""
            if is_dvd_subtitle_codec(codec):
                mkvextract = resolve_mkvextract()
                from pathlib import Path

                if not Path(mkvextract).is_file() and shutil.which(mkvextract) is None:
                    QMessageBox.warning(
                        self,
                        "Subtitles",
                        "mkvextract is required to extract DVD (VobSub) subtitles. "
                        "Bundle it next to mkvmerge or install MKVToolNix.",
                    )
                    return False
        return True

    def _start_bitmap_subtitle_conversion(self) -> None:
        if not self._bitmap_subtitle_prerequisites_ok() or self._project is None:
            return
        track = self._project.selected_track()
        settings = self._suggestions_panel.detection_settings()
        cancel = self._subtitle_job_runner.cancel_event
        project = self._project
        self._hold_prep_for_user_job()
        self._begin_subtitle_job(project, "ocr", "Converting bitmap subtitles to text")
        subtitle_settings = self._subtitles_panel.subtitle_settings()
        subtitle_streams = list((project.source.probe or {}).get("subtitle_streams") or [])

        def _run():
            cues, stream_index = convert_bitmap_stream_to_cues(
                project.source.path.resolve(),
                subtitle_streams,
                preferred_stream_index=subtitle_settings.bitmap_stream_index,
                audio_language=track.language if track else None,
                language_hint=settings.language,
                cancel_event=cancel,
                progress=lambda p, m: self._subtitle_job_runner.job_progress.emit(p, m),
            )
            return cues, stream_index

        if not self._subtitle_job_runner.run_callable("Bitmap subtitle OCR", _run):
            self._release_prep_after_user_job()
            self._abort_subtitle_job_start("Could not start bitmap subtitle conversion.")

    def _on_subtitle_ocr_job_result(self, result: object) -> None:
        project = self._release_subtitle_job()
        if project is None or not isinstance(result, tuple) or len(result) != 2:
            return
        cues, stream_index = result
        if not cues:
            QMessageBox.information(self, "Subtitles", "OCR returned no subtitle cues.")
            return
        project.subtitle_settings.bitmap_stream_index = int(stream_index)
        if not ingest_ocr_subtitles(project, cues):
            QMessageBox.information(self, "Subtitles", "Could not store OCR subtitle cues.")
            return
        self._refresh_subtitle_ui()
        self._notify_project_modified()
        self._subtitles_dock.raise_()
        self._status.showMessage(
            f"Converted bitmap subtitles ({source_label(SubtitleSource.OCR)}, stream #{stream_index})",
            6000,
        )

    def _on_subtitle_ocr_job_failed(self, message: str) -> None:
        if self._release_subtitle_job() is None:
            return
        QMessageBox.warning(self, "Bitmap subtitle conversion failed", message)

    def _on_subtitle_ocr_job_cancelled(self) -> None:
        if self._release_subtitle_job() is None:
            return
        self._status.showMessage("Bitmap subtitle conversion cancelled", 4000)

    def _offer_whisper_subtitle_generation(self, message: str) -> None:
        reply = QMessageBox.question(
            self,
            "Generate subtitles with Whisper?",
            message,
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.Yes,
        )
        if reply == QMessageBox.StandardButton.Yes:
            self._start_whisper_subtitle_generation()

    def _whisper_subtitle_prerequisites_ok(self) -> bool:
        if self._project is None or self._project.source is None:
            QMessageBox.information(self, "Subtitles", "Open media before generating subtitles.")
            return False
        if not self._project.source.path.is_file():
            QMessageBox.information(
                self,
                "Subtitles",
                "Media file is missing. Use File → Relink media…",
            )
            return False
        if self._project.selected_track() is None:
            QMessageBox.information(self, "Subtitles", "Select an audio track to transcribe.")
            return False
        if self._subtitle_job_runner.is_busy:
            QMessageBox.information(self, "Subtitles", "Wait for the current subtitle job to finish.")
            return False
        if self._ai_job_runner.is_busy:
            QMessageBox.information(self, "Subtitles", "Wait for the current AI job to finish.")
            return False
        if env_value("AI_PYTHON") is None and not is_whisper_available():
            QMessageBox.warning(
                self,
                "Whisper not available",
                f"faster-whisper is not installed.\n\n{whisper_install_hint()}",
            )
            return False
        ffmpeg_ok = any(t.name == "ffmpeg" and t.ok for t in check_toolchain().tools)
        if not ffmpeg_ok:
            QMessageBox.warning(self, "Subtitles", "ffmpeg is required to extract audio.")
            return False
        return True

    def _start_whisper_subtitle_generation(self) -> None:
        if not self._whisper_subtitle_prerequisites_ok() or self._project is None:
            return
        track = self._project.selected_track()
        assert track is not None
        settings = self._suggestions_panel.detection_settings()
        cancel = self._subtitle_job_runner.cancel_event
        project = self._project
        self._hold_prep_for_user_job()
        self._begin_subtitle_job(
            project,
            "whisper",
            f"Transcribing stream #{track.stream_index} ({track.codec})",
        )

        def _run() -> list[TranscriptWord]:
            return transcribe_project_track(
                project,
                model_size=settings.whisper_model,
                language=settings.language or track.language,
                cancel_event=cancel,
                progress=lambda p, m: self._subtitle_job_runner.job_progress.emit(p, m),
            )

        if not self._subtitle_job_runner.run_callable("Whisper subtitles", _run):
            self._release_prep_after_user_job()
            self._abort_subtitle_job_start("Could not start subtitle generation.")

    def _begin_subtitle_job(self, project: Project, kind: str, heading: str) -> None:
        self._subtitle_job_kind = kind
        self._subtitle_job_project = project
        self._subtitles_panel.show_job_banner(heading)
        self._status.showMessage(heading, 0)

    def _abort_subtitle_job_start(self, message: str) -> None:
        self._subtitle_job_kind = None
        self._subtitle_job_project = None
        self._subtitles_panel.hide_job_banner()
        self._subtitles_panel.set_busy(False)
        self._status.showMessage(message, 4000)
        QMessageBox.information(self, "Subtitles", message)

    def _release_subtitle_job(self) -> Project | None:
        project = getattr(self, "_subtitle_job_project", None)
        self._subtitle_job_project = None
        self._subtitle_job_kind = None
        self._subtitles_panel.hide_job_banner()
        if project is None or self._project is not project:
            return None
        return project

    def _on_subtitles_job_cancel(self) -> None:
        self._subtitles_panel.mark_job_cancelling()
        self._subtitle_job_runner.cancel_active()
        self._status.showMessage("Cancelling subtitle job…", 0)

    def _on_subtitle_job_log(self, message: str) -> None:
        self._subtitles_panel.note_job_detail(message)

    def _on_subtitle_job_progress(self, percent: int, message: str) -> None:
        self._subtitles_panel.set_job_progress(percent, message)
        detail = message.strip() or "Subtitles"
        self._status.showMessage(f"{percent}% {detail}", 0)

    def _on_subtitle_job_busy(self, busy: bool) -> None:
        self._subtitles_panel.set_busy(busy)
        if not busy:
            self._subtitles_panel.hide_job_banner()
            self._release_prep_after_user_job()

    def _on_subtitle_job_failed(self, message: str) -> None:
        kind = getattr(self, "_subtitle_job_kind", None)
        if kind == "ocr":
            self._on_subtitle_ocr_job_failed(message)
        elif kind == "reapply":
            self._on_subtitle_reapply_job_failed(message)
        else:
            self._on_subtitle_whisper_job_failed(message)

    def _on_subtitle_job_result(self, result: object) -> None:
        kind = getattr(self, "_subtitle_job_kind", None)
        if kind == "ocr":
            self._on_subtitle_ocr_job_result(result)
        elif kind == "reapply":
            self._on_subtitle_reapply_job_result(result)
        else:
            self._on_subtitle_whisper_job_result(result)

    def _on_subtitle_job_cancelled(self) -> None:
        kind = getattr(self, "_subtitle_job_kind", None)
        if kind == "ocr":
            self._on_subtitle_ocr_job_cancelled()
        elif kind == "reapply":
            self._on_subtitle_reapply_job_cancelled()
        else:
            self._on_subtitle_whisper_job_cancelled()

    def _on_subtitle_whisper_job_result(self, result: object) -> None:
        project = self._release_subtitle_job()
        if project is None or not isinstance(result, list):
            return
        words = [w for w in result if isinstance(w, TranscriptWord)]
        if not words:
            QMessageBox.information(self, "Subtitles", "Whisper returned no words.")
            return
        track = project.selected_track()
        if track is None:
            return
        settings = self._suggestions_panel.detection_settings()
        store_whisper_cache(
            project,
            stream_index=track.stream_index,
            model=settings.whisper_model,
            words=words,
        )
        if not ingest_whisper_subtitles(project):
            QMessageBox.information(self, "Subtitles", "Could not build subtitle cues.")
            return
        self._refresh_subtitle_ui()
        self._notify_project_modified()
        self._subtitles_dock.raise_()
        self._status.showMessage("Generated subtitles", 6000)

    def _on_subtitle_whisper_job_failed(self, message: str) -> None:
        if self._release_subtitle_job() is None:
            return
        QMessageBox.warning(self, "Subtitle generation failed", message)

    def _on_subtitle_whisper_job_cancelled(self) -> None:
        if self._release_subtitle_job() is None:
            return
        self._status.showMessage("Subtitle generation cancelled", 4000)

    def _delete_selected_subtitle_cue(self) -> None:
        cue = self._subtitles_panel.selected_cue()
        if cue is not None:
            self._on_subtitle_delete_cue_requested(cue.id)

    def _on_subtitle_cue_edited(self, cue_id: str, text: str) -> None:
        if self._project is None:
            return
        cue = self._find_subtitle_cue(cue_id)
        if cue is None:
            return
        before = cue.edited_text
        if text == before:
            return
        cue.edited_text = text
        cue.user_edited = True
        self._subtitles_panel.refresh_row_label(cue)
        self._subtitles_panel.refresh_selected_cue_fields()
        self._preview_panel.sync_subtitle_overlay()
        self._notify_project_modified()
        self._push_undo(
            "edit subtitle",
            undo=lambda: self._restore_subtitle_text(cue_id, before, False),
            redo=lambda: self._restore_subtitle_text(cue_id, text, True),
        )

    def _on_subtitles_reapply_ocr_cleanup(self) -> None:
        if self._project is None or not self._project.has_ocr_subtitles():
            return
        if self._subtitle_job_runner.is_busy:
            QMessageBox.information(
                self,
                "Subtitles",
                "Another subtitle task is already running.",
            )
            return
        self._start_reapply_ocr_cleanup()

    def _start_reapply_ocr_cleanup(self) -> None:
        if self._project is None:
            return

        project = self._project
        before = self._snapshot_ocr_cue_texts()
        cancel = self._subtitle_job_runner.cancel_event
        self._hold_prep_for_user_job()
        self._begin_subtitle_job(project, "reapply", "Reapplying OCR cleanup")

        def _run() -> tuple[int, dict[str, tuple[str, str, bool]]]:
            changed = reapply_ocr_cleanup(
                project,
                progress=lambda p, m: self._subtitle_job_runner.job_progress.emit(p, m),
                cancel_event=cancel,
            )
            return changed, before

        if not self._subtitle_job_runner.run_callable("Reapply OCR cleanup", _run):
            self._release_prep_after_user_job()
            self._abort_subtitle_job_start("Could not start OCR cleanup.")

    def _on_subtitle_reapply_job_result(self, result: object) -> None:
        project = self._release_subtitle_job()
        if project is None or not isinstance(result, tuple) or len(result) != 2:
            return
        changed, before = result
        if not isinstance(changed, int):
            return
        self._subtitles_panel.load_project(project)
        self._preview_panel.sync_subtitle_overlay()
        if not changed:
            if project.whisper_cache is None or not project.whisper_cache.words:
                self._status.showMessage(
                    "No OCR lines changed. Run AI detection or Generate from Whisper first "
                    "for audio-guided cleanup.",
                    8000,
                )
            else:
                self._status.showMessage("No OCR subtitle lines needed cleanup", 4000)
            return
        after = self._snapshot_ocr_cue_texts()
        self._notify_project_modified()
        self._push_undo(
            "reapply OCR cleanup",
            undo=lambda: self._restore_ocr_cue_texts(before),
            redo=lambda: self._restore_ocr_cue_texts(after),
        )
        self._status.showMessage(
            f"Reapplied OCR cleanup to {changed} cue{'s' if changed != 1 else ''}",
            6000,
        )

    def _on_subtitle_reapply_job_failed(self, message: str) -> None:
        if self._release_subtitle_job() is None:
            return
        QMessageBox.warning(self, "OCR cleanup failed", message)

    def _on_subtitle_reapply_job_cancelled(self) -> None:
        if self._release_subtitle_job() is None:
            return
        self._status.showMessage("OCR cleanup cancelled", 4000)

    def _snapshot_ocr_cue_texts(self) -> dict[str, tuple[str, str, bool]]:
        if self._project is None:
            return {}
        from corrcut.domain.subtitle_cue import SubtitleSource
        from corrcut.engine.subtitles.cue_sets import cues_for_source

        return {
            cue.id: (cue.text, cue.edited_text, cue.user_edited)
            for cue in cues_for_source(self._project, SubtitleSource.OCR)
        }

    def _restore_ocr_cue_texts(self, snapshot: dict[str, tuple[str, str, bool]]) -> None:
        if self._project is None:
            return
        from corrcut.domain.subtitle_cue import SubtitleSource
        from corrcut.engine.subtitles.cue_sets import cues_for_source

        for cue in cues_for_source(self._project, SubtitleSource.OCR):
            row = snapshot.get(cue.id)
            if row is None:
                continue
            cue.text, cue.edited_text, cue.user_edited = row
        self._subtitles_panel.load_project(self._project)
        self._preview_panel.sync_subtitle_overlay()
        self._notify_project_modified()

    def _snapshot_subtitle_cues(self) -> list[dict]:
        if self._project is None:
            return []
        return [cue.to_json() for cue in self._project.subtitle_cues]

    def _restore_subtitle_cues(self, snapshot: list[dict], *, select_cue_id: str | None = None) -> None:
        if self._project is None:
            return
        self._project.subtitle_cues = [SubtitleCue.from_json(row) for row in snapshot]
        self._subtitles_panel.load_project(self._project)
        if select_cue_id is not None:
            self._subtitles_panel.select_cue(select_cue_id)
        self._preview_panel.sync_subtitle_overlay()
        self._notify_project_modified()

    def _on_subtitle_add_cue_requested(self) -> None:
        if self._project is None:
            return
        before = self._snapshot_subtitle_cues()
        seconds = self._timeline.playhead_seconds()
        new_cue = add_cue_at(self._project, seconds)
        self._subtitles_panel.load_project(self._project)
        self._subtitles_panel.select_cue(new_cue.id)
        self._timeline.go_to_time(float(new_cue.start.seconds), scroll_into_view=True)
        self._preview_panel.sync_subtitle_overlay()
        self._notify_project_modified()
        after = self._snapshot_subtitle_cues()
        self._push_undo(
            "add subtitle cue",
            undo=lambda: self._restore_subtitle_cues(before),
            redo=lambda: self._restore_subtitle_cues(after, select_cue_id=new_cue.id),
        )

    def _on_subtitle_delete_cue_requested(self, cue_id: str) -> None:
        if self._project is None:
            return
        cue = self._find_subtitle_cue(cue_id)
        if cue is None:
            return
        before = self._snapshot_subtitle_cues()
        if not delete_cue(self._project, cue_id):
            return
        self._subtitles_panel.load_project(self._project)
        self._preview_panel.sync_subtitle_overlay()
        self._notify_project_modified()
        after = self._snapshot_subtitle_cues()
        self._push_undo(
            "delete subtitle cue",
            undo=lambda: self._restore_subtitle_cues(before, select_cue_id=cue.id),
            redo=lambda: self._restore_subtitle_cues(after),
        )

    def _on_subtitle_cue_reset(self, cue_id: str) -> None:
        if self._project is None:
            return
        cue = self._find_subtitle_cue(cue_id)
        if cue is None:
            return
        before = cue.edited_text
        before_flag = cue.user_edited
        auto = cue.text
        cue.edited_text = auto
        cue.user_edited = False
        self._subtitles_panel.select_cue(cue_id)
        self._subtitles_panel.load_project(self._project)
        self._preview_panel.sync_subtitle_overlay()
        self._notify_project_modified()
        self._push_undo(
            "reset subtitle",
            undo=lambda: self._restore_subtitle_text(cue_id, before, before_flag),
            redo=lambda: self._restore_subtitle_text(cue_id, auto, False),
        )

    def _find_subtitle_cue(self, cue_id: str) -> SubtitleCue | None:
        if self._project is None:
            return None
        for cue in self._project.subtitle_cues:
            if cue.id == cue_id:
                return cue
        return None

    def _restore_subtitle_text(self, cue_id: str, text: str, user_edited: bool) -> None:
        cue = self._find_subtitle_cue(cue_id)
        if cue is None:
            return
        cue.edited_text = text
        cue.user_edited = user_edited
        self._subtitles_panel.load_project(self._project)
        self._preview_panel.sync_subtitle_overlay()
        self._notify_project_modified()

    def _regenerate_subtitles_for_operations(self) -> None:
        if self._project is None or not self._project.subtitle_cues:
            return
        if on_operations_changed(self._project):
            self._subtitles_panel.load_project(self._project)
            self._preview_panel.sync_subtitle_overlay()

    def _sync_subtitles_to_playhead(self, seconds: float) -> None:
        if self._preview_panel.subtitle_overlay_editing:
            return
        self._subtitles_panel.follow_playhead(seconds)

    def _sync_dock_tables_to_playhead(self, seconds: float) -> None:
        self._sync_subtitles_to_playhead(seconds)
        self._suggestions_panel.follow_playhead(seconds)
        self._operations_panel.follow_playhead(seconds)
