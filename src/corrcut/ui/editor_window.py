"""Phase 2 editor — timeline, operations, project persistence."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QEvent, Qt, QTimer, QUrl
from PySide6.QtGui import QCloseEvent, QDragEnterEvent, QDragMoveEvent, QDropEvent, QKeyEvent
from PySide6.QtWidgets import (
    QApplication,
    QDockWidget,
    QLabel,
    QMainWindow,
    QPushButton,
    QStatusBar,
    QWidget,
)

from corrcut.config import APP_NAME, AppPaths, app_settings
from corrcut.domain.operation import OperationType
from corrcut.domain.project import Project
from corrcut.engine.jobs.job_runner import JobRunner
from corrcut.engine.waveform import PeakStore
from corrcut.io.file_drop import first_openable_path
from corrcut.preview.playback_profile import load_playback_profile
from corrcut.ui.buttons import use_pill_button
from corrcut.ui.closing_notice import ClosingNotice
from corrcut.ui.editor import (
    EditorAiMixin,
    EditorFilmstripMixin,
    EditorFlagsMixin,
    EditorMenusMixin,
    EditorOperationsMixin,
    EditorProjectMixin,
    EditorSceneCutsMixin,
    EditorSubtitlesMixin,
    EditorUndoMixin,
    EditorVideoMatteMixin,
    EditorWaveformMixin,
)
from corrcut.ui.export_progress_dialog import ExportProgressDialog
from corrcut.ui.operations_panel import OperationsPanel
from corrcut.ui.preview_panel import PreviewPanel
from corrcut.ui.stream_inspector import StreamInspector
from corrcut.ui.subtitles_panel import SubtitlesPanel
from corrcut.ui.suggestions_panel import SuggestionsPanel
from corrcut.ui.timeline.timecode import format_timecode
from corrcut.ui.timeline.timeline_mode import TimelineMode, tool_status_text
from corrcut.ui.timeline.timeline_panel import TimelinePanel

_KEY_MAIN_GEOMETRY = "mainwindow/geometry"
# Bumped so the side-dock tab default applies once. Later saves still win.
_KEY_MAIN_STATE = "mainwindow/state_v3"


def _local_drop_paths(mime: object) -> list[Path]:
    has_urls = getattr(mime, "hasUrls", None)
    urls = getattr(mime, "urls", None)
    if not callable(has_urls) or not callable(urls):
        return []
    try:
        if not has_urls():
            return []
        raw_urls = urls()
    except RuntimeError:
        # Synthetic/dropped QMimeData can already be deleted in tests or after the event.
        return []
    paths: list[Path] = []
    for url in raw_urls:
        if isinstance(url, QUrl) and url.isLocalFile():
            paths.append(Path(url.toLocalFile()))
    return paths


class EditorWindow(
    EditorMenusMixin,
    EditorProjectMixin,
    EditorWaveformMixin,
    EditorFilmstripMixin,
    EditorSceneCutsMixin,
    EditorVideoMatteMixin,
    EditorUndoMixin,
    EditorOperationsMixin,
    EditorFlagsMixin,
    EditorSubtitlesMixin,
    EditorAiMixin,
    QMainWindow,
):
    def __init__(self, paths: AppPaths) -> None:
        super().__init__()
        self._paths = paths
        self._project: Project | None = None
        self._peak_store: PeakStore | None = None
        self._peak_preload_token = 0
        self._waveform_generation = 0
        self._filmstrip_generation = 0
        self._filmstrip_pending: list[float] = []
        self._filmstrip_primary_count = 0
        self._filmstrip_in_flight: list[float] = []
        self._scene_cut_generation = 0
        self._prep_held_for_user_job = False
        self._scene_cuts_resume_after_user_job = False
        self._defer_token = 0
        self._duration = 1.0
        self._job_runner = JobRunner(self)
        self._ai_job_runner = JobRunner(self)
        self._subtitle_job_runner = JobRunner(self)
        self._subtitle_extract_job_runner = JobRunner(self)
        self._subtitle_extract_generation = 0
        self._embedded_extract_active = False
        self._embedded_extract_pending = False
        self._embedded_extract_show_cancel = False
        self._embedded_extract_job_generation = 0
        self._extract_cancel_generation = -1
        self._embedded_extract_from_user = False
        self._media_open_job_runner = JobRunner(self)
        self._media_open_dialog: ExportProgressDialog | None = None
        self._waveform_job_runner = JobRunner(self)
        self._filmstrip_job_runner = JobRunner(self)
        self._scene_cut_job_runner = JobRunner(self)
        self._subtitle_job_kind: str | None = None
        self._subtitle_job_project = None
        self._playhead_sync_from_preview = False
        self._timeline_scrub_active = False
        self._pending_preview_sync_after_scrub = False
        self._scrub_settle_timer = QTimer(self)
        self._scrub_settle_timer.setSingleShot(True)
        self._scrub_settle_timer.setInterval(200)
        self._scrub_settle_timer.timeout.connect(self._release_scrub_lock)
        # Trim-handle scrub: exact seeks (keyframes look wrong for in/out).
        self._pending_trim_seek_s: float | None = None
        self._trim_seek_timer = QTimer(self)
        self._trim_seek_timer.setSingleShot(True)
        self._trim_seek_timer.setInterval(40)
        self._trim_seek_timer.timeout.connect(self._flush_trim_preview_seek)
        self._preview_playing = False
        self._export_dialog = None
        self._ai_job_project = None
        self._corr_path: Path | None = None
        self._saved_project_snapshot: str | None = None
        self._project_dirty = False
        self._waveform_build_disabled = False
        self._waveform_build_failures = 0
        self._filmstrip_hole_retries = 0
        self._waveform_gate_open_for_scene_cuts = False
        self._scene_cuts_waiting_for_waveform = False
        self._preview_sync_suppressed = 0
        self._spatial_selection = "region"
        self._shuttle_k_held = False
        self._closing_application = False
        self._quit_in_progress = False
        self._quit_accepted = False
        self._closing_notice: ClosingNotice | None = None
        self._quit_deadline_monotonic = 0.0

        self._init_undo_stack()
        self.setWindowTitle(f"{APP_NAME}")
        self.resize(1200, 720)
        self.setAcceptDrops(True)
        self._apply_dock_drag_zone_styles()

        self._preview_panel = PreviewPanel()
        self._preview_panel.set_playback_profile(load_playback_profile())
        self.setCentralWidget(self._preview_panel)

        self._timeline_panel = TimelinePanel()
        self._timeline = self._timeline_panel.timeline
        self._timeline_dock = self._make_dock("Timeline", "TimelineDock", self._timeline_panel)
        self.addDockWidget(Qt.DockWidgetArea.BottomDockWidgetArea, self._timeline_dock)

        self._stream_inspector = StreamInspector()
        self._track_dock = self._make_dock("Streams", "StreamsDock", self._stream_inspector)

        self._operations_panel = OperationsPanel()
        self._ops_dock = self._make_dock("Operations", "OperationsDock", self._operations_panel)

        self._suggestions_panel = SuggestionsPanel()
        self._suggestions_dock = self._make_dock(
            "AI Suggestions", "SuggestionsDock", self._suggestions_panel
        )

        self._subtitles_panel = SubtitlesPanel()
        self._subtitles_dock = self._make_dock("Subtitles", "SubtitlesDock", self._subtitles_panel)
        self._place_side_docks()

        self._build_menus()
        self._init_flag_shortcuts()
        self._status = QStatusBar()
        self.setStatusBar(self._status)
        self._background_cancel_btn = QPushButton("Cancel")
        self._background_cancel_btn.setObjectName("backgroundJobCancel")
        self._background_cancel_btn.setToolTip("Cancel background jobs")
        use_pill_button(self._background_cancel_btn)
        self._background_cancel_btn.clicked.connect(self._cancel_background_jobs)
        self._background_cancel_btn.hide()
        self._status.addPermanentWidget(self._background_cancel_btn)
        self._duration_label = QLabel()
        self._duration_label.setObjectName("clipDurationStatus")
        self._duration_label.setToolTip("Clip duration")
        self._show_clip_duration(self._duration)
        self._tool_status = QLabel(tool_status_text(TimelineMode.SELECT))
        self._tool_status.setObjectName("timelineToolStatus")
        self._tool_status.setToolTip(
            "Timeline tool for the next draw, or the type applied to the selected edit"
        )
        self._status.addPermanentWidget(self._duration_label)
        self._status.addPermanentWidget(self._tool_status)
        self._timeline_panel.tool_label_changed.connect(self._tool_status.setText)

        self._wire_signals()
        self._timeline.set_timeline_mode(TimelineMode.SELECT)
        self._timeline.set_draw_operation_type(OperationType.MUTE)
        self._set_project(None)
        self._restore_window_layout()
        app = QApplication.instance()
        if app is not None:
            app.installEventFilter(self)

    def _sync_timeline_playback_filter(self) -> None:
        self._timeline.set_playback_filter(
            edits_on=self._preview_panel.edits_enabled(),
            profile=self._preview_panel.playback_profile(),
        )

    def _sync_timeline_tools(self) -> None:
        enabled = self._project is not None and self._project.source is not None
        self._timeline_panel.set_tools_enabled(enabled)
        self._timeline.set_edits_enabled(enabled)
        for action in self._timeline_tool_actions:
            action.setEnabled(enabled)

    def _show_clip_duration(self, seconds: float) -> None:
        if self._project is None or self._project.source is None:
            seconds = 0.0
        self._duration_label.setText(f"Length: {format_timecode(seconds)}")

    def _apply_dock_drag_zone_styles(self) -> None:
        self.setStyleSheet("""
            QMainWindow::separator {
                background: rgba(0, 0, 0, 0.55);
                width: 3px;
                height: 3px;
            }
            QMainWindow::separator:hover {
                background: rgba(140, 140, 140, 0.75);
            }
            QMainWindow::separator:pressed {
                background: rgba(255, 255, 255, 0.85);
            }
            QStatusBar::item {
                border: none;
            }
            QLabel#clipDurationStatus {
                padding-right: 16px;
            }
            QPushButton#backgroundJobCancel {
                margin-right: 8px;
            }
            """)

    def _window_settings(self):
        return app_settings()

    def _make_dock(self, title: str, object_name: str, widget) -> QDockWidget:
        dock = QDockWidget(title, self)
        dock.setObjectName(object_name)
        dock.setWidget(widget)
        dock.setFeatures(
            QDockWidget.DockWidgetFeature.DockWidgetMovable
            | QDockWidget.DockWidgetFeature.DockWidgetClosable
        )
        return dock

    def _side_docks(self) -> tuple[QDockWidget, ...]:
        return (
            self._ops_dock,
            self._track_dock,
            self._suggestions_dock,
            self._subtitles_dock,
        )

    def _place_side_docks(self) -> None:
        """One right-hand tab stack. Operations (edits) is the selected tab."""
        area = Qt.DockWidgetArea.RightDockWidgetArea
        for dock in self._side_docks():
            self.addDockWidget(area, dock)
            dock.show()
        previous = self._ops_dock
        for dock in self._side_docks()[1:]:
            self.tabifyDockWidget(previous, dock)
            previous = dock
        self._ops_dock.raise_()
        self._timeline_dock.show()

    def _restore_window_layout(self) -> None:
        settings = self._window_settings()
        geom = settings.value(_KEY_MAIN_GEOMETRY)
        if geom is not None:
            self.restoreGeometry(geom)
        state = settings.value(_KEY_MAIN_STATE)
        if state is not None:
            self.restoreState(state)
        for dock in (
            self._timeline_dock,
            self._track_dock,
            self._ops_dock,
            self._suggestions_dock,
            self._subtitles_dock,
        ):
            dock.setFloating(False)

    def _save_window_layout(self) -> None:
        settings = self._window_settings()
        settings.setValue(_KEY_MAIN_GEOMETRY, self.saveGeometry())
        settings.setValue(_KEY_MAIN_STATE, self.saveState())

    def _wire_signals(self) -> None:
        self._timeline.placeholder_span_changed.connect(self._on_placeholder_span)
        self._timeline.playhead_changed.connect(self._on_playhead)
        self._timeline.playhead_scrub_started.connect(self._on_playhead_scrub_started)
        self._timeline.playhead_scrub_finished.connect(self._on_playhead_scrub_finished)
        self._timeline.trim_preview_seek.connect(self._on_trim_preview_seek)
        self._timeline.trim_preview_finished.connect(self._on_trim_preview_finished)
        self._preview_panel.position_changed.connect(self._on_preview_position)
        self._preview_panel.playback_state_changed.connect(self._on_preview_playback_state)
        self._preview_panel.about_to_play.connect(self._flush_filters_before_play)
        self._preview_panel.playback_filter_changed.connect(self._sync_timeline_playback_filter)
        self._sync_timeline_playback_filter()
        self._timeline.operation_created.connect(self._on_operation_created)
        self._timeline.operation_moved.connect(self._on_operation_moved)
        self._timeline.pose_selected.connect(self._on_timeline_pose_selected)
        self._timeline.operation_params_changed.connect(self._on_operation_params_changed)
        self._timeline.operation_selected.connect(self._on_timeline_region_selected)
        self._timeline.operation_deselected.connect(self._on_timeline_region_deselected)
        self._timeline.operation_delete_requested.connect(self._delete_selected_operation)
        self._timeline.flag_delete_requested.connect(self._delete_flag)
        self._timeline.flag_moved.connect(self._on_flag_moved)
        self._timeline.flag_context_menu_requested.connect(self._on_flag_context_menu)
        self._preview_panel.flag_requested.connect(self._drop_flag)
        self._preview_panel.open_media_requested.connect(self._open_media)
        self._preview_panel.open_project_requested.connect(self._open_project)
        self._timeline.operation_toggle_enabled_requested.connect(
            self._operations_panel.toggle_enabled
        )
        self._timeline.draw_operation_type_changed.connect(self._sync_draw_type_combo)
        self._timeline.timeline_mode_changed.connect(self._on_timeline_mode_from_widget)
        self._timeline_panel.tool_mode_changed.connect(self._on_tool_strip_mode)
        self._timeline_panel.beep_tone_changed.connect(self._on_beep_tone_changed)
        self._stream_inspector.track_selected.connect(self._on_track_selected)
        self._timeline_panel.audio_track_selected.connect(self._on_track_selected)
        self._timeline_panel.audio_language_selected.connect(self._on_audio_language_chosen)
        self._subtitles_panel.subtitle_settings_changed.connect(self._on_subtitle_settings_changed)
        self._subtitles_panel.refresh_requested.connect(self._on_subtitles_refresh_requested)
        self._subtitles_panel.generate_requested.connect(self._on_subtitles_generate_requested)
        self._subtitles_panel.convert_bitmap_requested.connect(self._on_convert_bitmap_requested)
        self._subtitles_panel.cue_source_selected.connect(self._on_cue_source_selected)
        self._subtitles_panel.reapply_ocr_cleanup_requested.connect(
            self._on_subtitles_reapply_ocr_cleanup
        )
        self._subtitles_panel.cue_edited.connect(self._on_subtitle_cue_edited)
        self._subtitles_panel.cue_reset.connect(self._on_subtitle_cue_reset)
        self._subtitles_panel.add_cue_requested.connect(self._on_subtitle_add_cue_requested)
        self._subtitles_panel.delete_cue_requested.connect(self._on_subtitle_delete_cue_requested)
        self._preview_panel.subtitle_cue_edited.connect(self._on_subtitle_cue_edited)
        self._subtitles_panel.cue_seek.connect(
            lambda seconds: self._timeline.go_to_time(seconds, scroll_into_view=True)
        )
        self._operations_panel.delete_requested.connect(self._delete_operation)
        self._operations_panel.selection_changed.connect(self._on_op_selection)
        self._operations_panel.censor_params_changed.connect(self._on_censor_params_changed)
        self._operations_panel.channels_changed.connect(self._on_channels_changed)
        self._operations_panel.enabled_changed.connect(self._on_enabled_changed)
        self._operations_panel.operation_meta_changed.connect(self._on_operation_meta_changed)
        self._preview_panel.video_region_box_changed.connect(self._on_video_region_box_changed)
        self._preview_panel.video_region_endpoint_changed.connect(
            self._on_video_region_endpoint_changed
        )
        self._preview_panel.video_region_keyframe_index_changed.connect(
            self._on_video_region_keyframe_index_changed
        )
        self._preview_panel.video_region_add_keyframe_requested.connect(
            self._on_video_region_add_keyframe
        )
        self._preview_panel.video_region_add_keyframe_at_requested.connect(
            self._on_video_region_add_keyframe
        )
        self._preview_panel.video_region_remove_keyframe_requested.connect(
            self._on_video_region_remove_keyframe
        )
        self._preview_panel.video_region_key_moved.connect(self._on_video_region_key_moved)
        self._preview_panel.keyframe_track_seek_requested.connect(self._on_keyframe_track_seek)
        self._preview_panel.keyframe_track_seek_finished.connect(
            self._on_keyframe_track_seek_finished
        )
        self._preview_panel.keyframe_track_background_pressed.connect(
            self._on_keyframe_track_background_pressed
        )

        self._suggestions_panel.generate_requested.connect(self._generate_suggestions)
        self._suggestions_panel.seek_requested.connect(self._on_suggestion_seek)
        self._suggestions_panel.accept_requested.connect(self._accept_suggestion)
        self._suggestions_panel.reject_requested.connect(self._reject_suggestion)
        self._timeline.suggestion_accept_requested.connect(self._accept_suggestion)
        self._timeline.suggestion_reject_requested.connect(self._reject_suggestion)
        self._suggestions_panel.pending_requested.connect(self._reset_suggestion_to_pending)
        self._suggestions_panel.job_cancel_requested.connect(self._on_suggestions_job_cancel)
        self._subtitles_panel.job_cancel_requested.connect(self._on_subtitles_job_cancel)

        self._job_runner.job_log.connect(self._on_main_job_log)
        self._job_runner.job_progress.connect(self._on_main_job_progress)
        self._job_runner.job_error.connect(self._on_main_job_failed)
        self._job_runner.job_result.connect(self._on_main_job_result)
        self._job_runner.job_cancelled.connect(self._on_main_job_cancelled)
        self._job_runner.busy_changed.connect(self._on_main_job_busy)
        self._job_runner.busy_changed.connect(self._sync_background_job_cancel)

        self._ai_job_runner.job_log.connect(self._on_ai_job_log)
        self._ai_job_runner.job_progress.connect(self._on_ai_job_progress)
        self._ai_job_runner.job_error.connect(self._on_ai_job_failed)
        self._ai_job_runner.job_result.connect(self._on_ai_job_result)
        self._ai_job_runner.job_cancelled.connect(self._on_ai_job_cancelled)
        self._ai_job_runner.busy_changed.connect(self._on_ai_job_busy)
        self._ai_job_runner.busy_changed.connect(self._sync_background_job_cancel)

        self._subtitle_job_runner.job_log.connect(self._on_subtitle_job_log)
        self._subtitle_job_runner.job_progress.connect(self._on_subtitle_job_progress)
        self._subtitle_job_runner.job_error.connect(self._on_subtitle_job_failed)
        self._subtitle_job_runner.job_result.connect(self._on_subtitle_job_result)
        self._subtitle_job_runner.job_cancelled.connect(self._on_subtitle_job_cancelled)
        self._subtitle_job_runner.busy_changed.connect(self._on_subtitle_job_busy)
        self._subtitle_job_runner.busy_changed.connect(self._sync_background_job_cancel)

        self._subtitle_extract_job_runner.job_error.connect(self._on_embedded_extract_failed)
        self._subtitle_extract_job_runner.job_result.connect(self._on_embedded_extract_result)
        self._subtitle_extract_job_runner.job_cancelled.connect(self._on_embedded_extract_cancelled)
        self._subtitle_extract_job_runner.busy_changed.connect(self._sync_background_job_cancel)

        self._waveform_job_runner.job_log.connect(lambda m: self._status.showMessage(m, 3000))
        self._waveform_job_runner.job_error.connect(self._on_waveform_job_failed)
        self._waveform_job_runner.job_result.connect(self._on_waveform_job_result)
        self._waveform_job_runner.busy_changed.connect(self._on_waveform_job_busy)
        self._waveform_job_runner.busy_changed.connect(self._sync_background_job_cancel)

        self._timeline.filmstrip_tiles_needed.connect(self._on_filmstrip_tiles_needed)
        self._filmstrip_job_runner.job_error.connect(self._on_filmstrip_job_failed)
        self._filmstrip_job_runner.job_result.connect(self._on_filmstrip_job_result)
        self._filmstrip_job_runner.busy_changed.connect(self._on_filmstrip_job_busy)
        self._filmstrip_job_runner.busy_changed.connect(self._sync_background_job_cancel)

        self._scene_cut_job_runner.job_error.connect(self._on_scene_cut_job_failed)
        self._scene_cut_job_runner.job_result.connect(self._on_scene_cut_job_result)
        self._scene_cut_job_runner.job_cancelled.connect(self._on_scene_cut_job_cancelled)
        self._scene_cut_job_runner.busy_changed.connect(self._on_scene_cut_job_busy)
        self._scene_cut_job_runner.busy_changed.connect(self._sync_background_job_cancel)

        self._media_open_job_runner.job_log.connect(self._on_media_open_log)
        self._media_open_job_runner.job_progress.connect(self._on_media_open_progress)
        self._media_open_job_runner.job_error.connect(self._on_media_open_failed)
        self._media_open_job_runner.job_result.connect(self._on_media_open_result)
        self._media_open_job_runner.job_cancelled.connect(self._on_media_open_cancelled)

    def _set_project(
        self,
        project: Project | None,
        *,
        subtitles_ready: bool = False,
        defer_ui: bool = True,
    ) -> None:
        self._invalidate_embedded_subtitle_extract()
        self._project = project
        self._peak_store = None
        self._peak_store_track_key = None
        self._waveform_switch_pending = False
        self._peak_preload_token += 1
        self._waveform_generation += 1
        self._waveform_build_disabled = False
        self._waveform_build_failures = 0
        self._filmstrip_hole_retries = 0
        self._waveform_gate_open_for_scene_cuts = False
        self._scene_cuts_waiting_for_waveform = False
        self._waveform_job_runner.cancel_active()
        self._filmstrip_generation += 1
        self._filmstrip_pending = []
        self._filmstrip_primary_count = 0
        self._filmstrip_in_flight = []
        self._filmstrip_job_runner.cancel_active()
        self._scene_cut_generation += 1
        self._scene_cut_job_runner.cancel_active()
        self._timeline.clear_filmstrip()
        self._timeline.set_scene_cut_times(())
        if project is None:
            self._timeline.set_peak_store(None, 1.0)
            self._timeline.set_playhead(0.0)
            self._timeline.sync_operations([])
            self._timeline.sync_suggestions([])
            self._timeline.sync_flags([], {})
            self._stream_inspector.load_project(Project())
            self._operations_panel.load_operations([])
            self._preview_panel.set_project(None)
            self._suggestions_panel.load_suggestions([])
            self._suggestions_panel.set_busy(False)
            self._subtitles_panel.load_project(None)
            self._show_clip_duration(0.0)
        else:
            self._preview_sync_suppressed += 1
            try:
                self._set_project_loaded(
                    project,
                    subtitles_ready=subtitles_ready,
                    defer_ui=defer_ui,
                )
            finally:
                self._preview_sync_suppressed -= 1
        self._sync_timeline_tools()
        self._sync_audio_track_button()
        self._sync_relink_action()
        self._sync_preserve_letterbox_action()
        self._clear_undo_stack()
        if project is None:
            self._saved_project_snapshot = None
            self._project_dirty = False
        else:
            self._establish_save_baseline()
        self._update_window_title()
        if project is not None and project.source is not None and not project.source.path.is_file():
            self._status.showMessage(
                f"Media missing: {project.source.path} — use File → Relink media",
                8000,
            )

    def _on_placeholder_span(self, seconds: float) -> None:
        if self._project is not None and self._project.source is not None:
            return
        self._duration = seconds
        self._show_clip_duration(seconds)

    def _set_project_loaded(
        self,
        project: Project,
        *,
        subtitles_ready: bool = False,
        defer_ui: bool = True,
    ) -> None:
        if project.source is not None:
            probe_data = project.source.probe or {}
            self._duration = float(probe_data.get("duration_seconds") or 1.0)
            self._show_clip_duration(self._duration)
            self._timeline.set_duration(self._duration)
        track = project.selected_track()
        if track is not None:
            self._timeline.set_sample_rate(track.sample_rate or 48000)
        self._timeline.sync_operations(project.timeline_operations())
        self._refresh_flags()
        self._timeline.go_to_start(notify=False)
        self._timeline_panel.sync_playhead_display(0.0)
        self._stream_inspector.load_project(project)
        self._refresh_suggestions_panel()
        self._preview_panel.set_project(project)
        self._schedule_deferred(0, self._ensure_filmstrip)
        self._schedule_deferred(0, self._ensure_scene_cuts)
        if not defer_ui:
            # Caller fills the lists while a progress dialog is still visible.
            return
        self._schedule_deferred(
            0,
            lambda: self._refresh_operations_panel(
                sync_timeline=False,
                sync_preview=False,
            ),
        )
        if subtitles_ready:
            self._schedule_deferred(0, self._present_loaded_subtitles)
        else:
            self._schedule_deferred(0, self._load_subtitles_on_idle)
        self._schedule_deferred(0, self._ensure_video_matte_and_refresh)

    def _present_loaded_subtitles(self) -> None:
        """Show cues already extracted while the open dialog was up."""
        if self._project is None:
            return
        self._subtitles_panel.load_project(self._project)
        self._preview_panel.sync_subtitle_overlay()

    def _load_subtitles_on_idle(self) -> None:
        if self._project is None:
            return
        self._ingest_subtitles_on_load()
        self._subtitles_panel.load_project(self._project)
        self._preview_panel.sync_subtitle_overlay()

    def _preview_toggle_play(self) -> None:
        self._preview_panel.toggle_playback()

    def _go_to_project_start(self) -> None:
        self._timeline.go_to_start(notify=True)
        self._timeline_panel.sync_playhead_display(0.0)

    def _on_main_job_log(self, message: str) -> None:
        self._status.showMessage(message, 3000)

    def _on_main_job_progress(self, percent: int, message: str) -> None:
        self._status.showMessage(f"{percent}% {message}", 2000)

    def _on_main_job_result(self, result: object) -> None:
        return

    def _on_main_job_failed(self, message: str) -> None:
        self._status.showMessage(f"Job failed: {message}", 6000)

    def _on_main_job_cancelled(self) -> None:
        self._status.showMessage("Job cancelled", 4000)

    def _on_main_job_busy(self, busy: bool) -> None:
        return

    def _go_to_project_end(self) -> None:
        self._timeline.go_to_time(self._duration, scroll_into_view=True)

    def _preview_stop(self) -> None:
        if self._focus_blocks_edit_shortcut():
            return
        self._preview_panel.stop_shuttle()

    def closeEvent(self, event: QCloseEvent) -> None:  # noqa: N802
        super().closeEvent(event)
        if event.isAccepted():
            app = QApplication.instance()
            if app is not None:
                app.removeEventFilter(self)

    def eventFilter(self, watched: object, event: QEvent) -> bool:  # noqa: N802
        if self._handle_empty_file_drop(watched, event):
            return True
        if self._handle_shuttle_key(event):
            return True
        return super().eventFilter(watched, event)

    def _handle_empty_file_drop(self, watched: object, event: QEvent) -> bool:
        """Open a dropped project or media file while the editor is empty."""
        if event.type() not in (
            QEvent.Type.DragEnter,
            QEvent.Type.DragMove,
            QEvent.Type.DragLeave,
            QEvent.Type.Drop,
        ):
            return False
        if not isinstance(watched, QWidget) or watched.window() is not self:
            return False
        if not self._can_open_dropped_file():
            return False
        if event.type() == QEvent.Type.DragLeave:
            return False
        if not isinstance(event, (QDragEnterEvent, QDragMoveEvent, QDropEvent)):
            return False
        path = first_openable_path(_local_drop_paths(event.mimeData()))
        if path is None:
            return False
        event.acceptProposedAction()
        if event.type() == QEvent.Type.Drop:
            self._open_dropped_file(path)
        return True

    def _handle_shuttle_key(self, event: QEvent) -> bool:
        """JKL shuttle. K is held for slow motion; text fields keep the letters."""
        if event.type() == QEvent.Type.ApplicationDeactivate:
            self._shuttle_k_held = False
            return False
        if not isinstance(event, QKeyEvent):
            return False
        if not self._shuttle_keys_reach_this_window():
            return False
        if self._focus_blocks_edit_shortcut():
            if event.type() == QEvent.Type.KeyRelease and event.key() == Qt.Key.Key_K:
                self._shuttle_k_held = False
            return False
        key = event.key()
        if key not in (Qt.Key.Key_J, Qt.Key.Key_K, Qt.Key.Key_L):
            return False
        blocked = (
            Qt.KeyboardModifier.ControlModifier
            | Qt.KeyboardModifier.AltModifier
            | Qt.KeyboardModifier.MetaModifier
            | Qt.KeyboardModifier.ShiftModifier
        )
        if event.modifiers() & blocked:
            return False
        if key == Qt.Key.Key_K:
            if event.type() == QEvent.Type.KeyRelease:
                if not event.isAutoRepeat():
                    self._shuttle_k_held = False
                return True
            if event.isAutoRepeat():
                return True
            self._shuttle_k_held = True
            self._preview_panel.stop_shuttle()
            return True
        if event.type() != QEvent.Type.KeyPress:
            return False
        direction = -1 if key == Qt.Key.Key_J else 1
        self._preview_panel.shuttle(direction, slow=self._shuttle_k_held)
        return True

    def _shuttle_keys_reach_this_window(self) -> bool:
        app = QApplication.instance()
        if app is None:
            return False
        active = app.activeWindow()
        fullscreen = getattr(self._preview_panel, "_fullscreen_window", None)
        if active is self or (fullscreen is not None and active is fullscreen):
            return True
        focus = app.focusWidget()
        if focus is None:
            return False
        if focus is self or self.isAncestorOf(focus):
            return True
        return fullscreen is not None and (focus is fullscreen or fullscreen.isAncestorOf(focus))

    def _preview_toggle_fullscreen(self) -> None:
        self._preview_panel.toggle_fullscreen()

    def _toggle_preview_edits(self) -> None:
        self._preview_panel.toggle_edits()

    def _release_scrub_lock(self) -> None:
        self._end_timeline_scrub()

    def _queue_trim_preview_seek(self, seconds: float) -> None:
        """Exact-seek preview while dragging in/out handles (leading + trailing)."""
        self._pending_trim_seek_s = max(0.0, float(seconds))
        if not self._trim_seek_timer.isActive():
            self._flush_trim_preview_seek()
            self._trim_seek_timer.start()

    def _flush_trim_preview_seek(self) -> None:
        seconds = self._pending_trim_seek_s
        self._pending_trim_seek_s = None
        if seconds is None:
            return
        self._preview_panel.seek(seconds, reference="absolute+exact")

    def _end_timeline_scrub(self, *, restore_seek: bool = True) -> None:
        """Drop the scrub lock so Play / Start / End can exact-seek and play."""
        self._scrub_settle_timer.stop()
        self._trim_seek_timer.stop()
        self._pending_trim_seek_s = None
        self._timeline_scrub_active = False
        if self._pending_preview_sync_after_scrub:
            self._pending_preview_sync_after_scrub = False
            self._preview_panel.sync_track_and_filters(restore_seek=restore_seek)
        self._preview_panel.schedule_video_region_relayout()

    def _flush_filters_before_play(self) -> None:
        """Apply deferred lavfi before Play without a seek that would pause mpv."""
        self._scrub_settle_timer.stop()
        self._trim_seek_timer.stop()
        self._pending_trim_seek_s = None
        self._timeline_scrub_active = False
        self._preview_panel.cancel_pending_seeks()
        if self._pending_preview_sync_after_scrub:
            self._pending_preview_sync_after_scrub = False
            self._preview_panel.sync_track_and_filters(restore_seek=False)

    def _on_playhead_scrub_started(self) -> None:
        self._scrub_settle_timer.stop()
        self._trim_seek_timer.stop()
        self._pending_trim_seek_s = None
        self._timeline_scrub_active = True

    def _on_trim_preview_seek(self, seconds: float) -> None:
        """Show the frame at the trim edge; leave the timeline playhead parked."""
        if self._playhead_sync_from_preview:
            return
        seek_s = max(0.0, float(seconds))
        if self._timeline.active_trim_endpoint() == "end":
            # Stay just inside the region so the edit is still visible.
            seek_s = max(0.0, seek_s - 0.04)
        self._preview_panel.set_keyframe_playhead(seek_s)
        self._follow_spatial_overlay(seek_s)
        self._queue_trim_preview_seek(seek_s)

    def _on_trim_preview_finished(self, _seconds: float) -> None:
        if self._playhead_sync_from_preview:
            return
        self._trim_seek_timer.stop()
        self._pending_trim_seek_s = None
        # Restore preview to the parked playhead (trim scrub was preview-only).
        seek_s = max(0.0, float(self._timeline.playhead_seconds()))
        self._preview_panel.set_keyframe_playhead(seek_s)
        self._follow_spatial_overlay(seek_s)
        self._preview_panel.seek(seek_s, reference="absolute+exact")
        self._scrub_settle_timer.start()

    def _on_playhead(self, seconds: float) -> None:
        if self._playhead_sync_from_preview:
            return
        self._preview_panel.set_keyframe_playhead(seconds)
        self._follow_spatial_overlay(seconds)
        self._sync_dock_tables_to_playhead(seconds)
        if self._timeline_scrub_active:
            self._preview_panel.seek(seconds, reference="absolute+keyframes")
        else:
            self._preview_panel.seek(seconds, reference="absolute+exact")

    def _on_playhead_scrub_finished(self, seconds: float) -> None:
        if self._playhead_sync_from_preview:
            return
        self._trim_seek_timer.stop()
        self._pending_trim_seek_s = None
        self._sync_dock_tables_to_playhead(seconds)
        self._preview_panel.seek(seconds, reference="absolute+exact")
        self._timeline_panel.sync_playhead_display(seconds)
        # Hold off mpv echo until the exact seek settles (avoids playhead snap-back).
        self._scrub_settle_timer.start()

    def _on_preview_position(self, seconds: float) -> None:
        if self._timeline_scrub_active:
            return
        self._playhead_sync_from_preview = True
        try:
            shown = self._preview_panel.exact_seek_target()
            if shown is None:
                shown = seconds
            self._timeline_panel.sync_playhead_display(
                shown,
                auto_scroll=self._preview_playing,
            )
            self._follow_spatial_overlay(shown)
            self._sync_dock_tables_to_playhead(shown)
        finally:
            self._playhead_sync_from_preview = False

    def _on_preview_playback_state(self, playing: bool) -> None:
        self._preview_playing = playing
        if playing:
            self._scrub_settle_timer.stop()
            self._trim_seek_timer.stop()
            self._pending_trim_seek_s = None
            self._timeline_scrub_active = False
            self._preview_panel.cancel_pending_seeks()
