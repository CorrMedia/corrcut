"""Subtitle cue list editor (Phase 7b)."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from corrcut.ai.censor_text import contains_profanity
from corrcut.ai.subtitles import (
    is_bitmap_subtitle_codec,
    is_ocr_subtitle_codec,
    is_text_subtitle_codec,
    select_bitmap_subtitle_stream_index,
)
from corrcut.ai.swear_lexicon import SwearLexicon
from corrcut.domain.project import Project
from corrcut.domain.subtitle_cue import SubtitleCue
from corrcut.domain.subtitle_settings import SubtitleSettings
from corrcut.engine.probe import probe_result_from_dict
from corrcut.engine.subtitles.cue_sets import (
    coerce_subtitle_source,
    source_label,
    switch_active_source,
)
from corrcut.engine.subtitles.display import SubtitleCueCursor
from corrcut.ui.bitmap_convert_dialog import BitmapConvertDialog, BitmapConvertRequest
from corrcut.ui.buttons import use_pill_button
from corrcut.ui.job_progress_banner import JobProgressBanner
from corrcut.ui.subtitle_cue_list import SubtitleCueList

_CONVERT_BITMAP_TOOLTIP = (
    "OCR a bitmap subtitle track into editable text cues. "
    "You choose the track when converting. "
    "Requires launching from the project .venv (Python 3.12/3.13) after "
    ".\\scripts\\install-ocr.ps1."
)


class SubtitlesPanel(QWidget):
    cue_selected = Signal(str)
    cue_seek = Signal(float)
    cue_edited = Signal(str, str)
    cue_reset = Signal(str)
    add_cue_requested = Signal()
    delete_cue_requested = Signal(str)
    refresh_requested = Signal()
    generate_requested = Signal()
    convert_bitmap_requested = Signal()
    reapply_ocr_cleanup_requested = Signal()
    cue_source_selected = Signal(object)
    subtitle_settings_changed = Signal(object)
    job_cancel_requested = Signal()

    def __init__(self) -> None:
        super().__init__()
        self._project: Project | None = None
        self._loading = False
        self._syncing_playhead = False
        self._cached_settings = SubtitleSettings()
        self._has_text_streams = False
        self._has_bitmap_streams = False
        self._busy = False
        self._profanity_lexicon: SwearLexicon | None = None
        self._playhead_cursor = SubtitleCueCursor()

        self._sub_stream = QComboBox()
        self._sub_stream.currentIndexChanged.connect(self._emit_subtitle_settings)

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("<b>Subtitles</b>"))

        self._cue_source_row = QWidget()
        cue_source_layout = QHBoxLayout(self._cue_source_row)
        cue_source_layout.setContentsMargins(0, 0, 0, 0)
        cue_source_layout.addWidget(QLabel("Active cue source:"))
        self._cue_source = QComboBox()
        self._cue_source.setToolTip(
            "Choose which subtitle generation to edit and export. "
            "Embedded text is preferred when available."
        )
        self._cue_source.currentIndexChanged.connect(self._on_cue_source_changed)
        cue_source_layout.addWidget(self._cue_source, stretch=1)
        self._active_source_label = QLabel()
        self._active_source_label.setStyleSheet("color: #888;")
        cue_source_layout.addWidget(self._active_source_label)
        layout.addWidget(self._cue_source_row)
        self._cue_source_row.setVisible(False)

        btn_row = QHBoxLayout()
        self._refresh_btn = QPushButton("Refresh from source")
        use_pill_button(self._refresh_btn)
        self._refresh_btn.clicked.connect(self.refresh_requested.emit)
        self._generate_btn = QPushButton("Generate from Whisper")
        use_pill_button(self._generate_btn)
        self._generate_btn.clicked.connect(self.generate_requested.emit)
        self._convert_bitmap_btn = QPushButton("Convert from bitmap…")
        use_pill_button(self._convert_bitmap_btn)
        self._convert_bitmap_btn.setToolTip(_CONVERT_BITMAP_TOOLTIP)
        self._convert_bitmap_btn.clicked.connect(self.convert_bitmap_requested.emit)
        self._reapply_ocr_btn = QPushButton("Reapply OCR cleanup")
        use_pill_button(self._reapply_ocr_btn)
        self._reapply_ocr_btn.setToolTip(
            "Re-run OCR cleanup on bitmap cues: formatting fixes plus Whisper audio "
            "alignment when a transcript cache exists (from AI detection or "
            "Generate from Whisper)."
        )
        self._reapply_ocr_btn.clicked.connect(self.reapply_ocr_cleanup_requested.emit)
        self._settings_btn = QToolButton()
        self._settings_btn.setText("Settings…")
        use_pill_button(self._settings_btn)
        self._settings_btn.setToolTip("Subtitle ingest and preview options")
        self._settings_btn.clicked.connect(self._open_settings_dialog)
        btn_row.addWidget(self._refresh_btn)
        btn_row.addWidget(self._generate_btn)
        btn_row.addWidget(self._convert_bitmap_btn)
        btn_row.addWidget(self._reapply_ocr_btn)
        btn_row.addWidget(self._settings_btn)
        btn_row.addStretch()
        layout.addLayout(btn_row)

        self._cue_list = SubtitleCueList()
        self._cue_list.current_cue_changed.connect(self._on_selection_changed)
        self._cue_list.cue_activated.connect(self._on_cue_activated)
        self._cue_list.reset_requested.connect(self.cue_reset.emit)
        self._cue_list.delete_requested.connect(self.delete_cue_requested.emit)
        self._cue_list.text_edited.connect(self._on_text_edited)

        cue_ops_row = QHBoxLayout()
        self._add_cue_btn = QPushButton("Insert cue at playhead")
        use_pill_button(self._add_cue_btn)
        self._add_cue_btn.setToolTip("Insert a new empty cue at the current timeline playhead")
        self._add_cue_btn.clicked.connect(self.add_cue_requested.emit)
        self._cue_search = QLineEdit()
        self._cue_search.setPlaceholderText("Search cues…")
        self._cue_search.setClearButtonEnabled(True)
        self._cue_search.textChanged.connect(self._apply_cue_filter)
        self._profanity_filter = QCheckBox("Only profane")
        self._profanity_filter.setToolTip(
            "Show only cues containing words matched by the profanity lexicon"
        )
        self._profanity_filter.toggled.connect(self._apply_cue_filter)
        cue_ops_row.addWidget(self._add_cue_btn)
        cue_ops_row.addStretch()
        cue_ops_row.addWidget(self._profanity_filter)
        cue_ops_row.addWidget(self._cue_search)

        layout.addWidget(QLabel("<b>Cues</b>"))
        self._job_banner = JobProgressBanner()
        self._job_banner.cancel_requested.connect(self.job_cancel_requested.emit)
        layout.addWidget(self._job_banner)
        self._extract_status = QLabel()
        self._extract_status.setWordWrap(True)
        self._extract_status.setStyleSheet("color: #888;")
        self._extract_status.setVisible(False)
        layout.addWidget(self._extract_status)
        layout.addWidget(self._cue_list, stretch=1)
        layout.addLayout(cue_ops_row)

        self._settings_dialog = self._build_settings_dialog()

    def _build_settings_dialog(self) -> QDialog:
        dialog = QDialog(self)
        dialog.setWindowTitle("Subtitle settings")
        layout = QVBoxLayout(dialog)
        form = QFormLayout()
        form.addRow("Embedded source track", self._sub_stream)
        layout.addLayout(form)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        return dialog

    def _on_cue_source_changed(self) -> None:
        if self._loading or self._project is None:
            return
        source = coerce_subtitle_source(self._cue_source.currentData())
        if source is None:
            return
        if not switch_active_source(self._project, source):
            return
        self._loading = True
        try:
            self._reload_active_cue_table()
            self._active_source_label.setText(source_label(source))
        finally:
            self._loading = False
        self.cue_source_selected.emit(source)

    def _reload_active_cue_table(self) -> None:
        if self._project is None:
            return
        cues = self._project.subtitle_cues
        if not cues:
            self._cue_list.set_cues([])
            self._cue_list.setEnabled(False)
            return
        self._cue_list.setEnabled(True)
        self._apply_cue_filter()

    def _populate_cue_source_controls(self, project: Project) -> None:
        sources = project.available_subtitle_sources()
        self._cue_source.blockSignals(True)
        self._cue_source.clear()
        for source in sources:
            count = len(project.subtitle_cue_sets.get(source.value, []))
            self._cue_source.addItem(
                f"{source_label(source)} ({count} cues)",
                source,
            )
        active = project.active_subtitle_source
        if active is not None:
            for row in range(self._cue_source.count()):
                if coerce_subtitle_source(self._cue_source.itemData(row)) == active:
                    self._cue_source.setCurrentIndex(row)
                    break
        self._cue_source.blockSignals(False)
        has_sources = len(sources) > 0
        self._cue_source_row.setVisible(has_sources)
        self._cue_source.setEnabled(has_sources and not self._busy)
        if active is not None and has_sources:
            self._active_source_label.setText(source_label(active))
        else:
            self._active_source_label.clear()

    def _open_settings_dialog(self) -> None:
        self._settings_dialog.exec()

    @staticmethod
    def _format_bitmap_stream_label(stream) -> str:
        lang = f" ({stream.language})" if stream.language else ""
        title = f" — {stream.title}" if stream.title else ""
        return f"#{stream.index} {stream.codec}{lang}{title}"

    def resolved_bitmap_stream_label(self) -> str | None:
        if self._project is None or self._project.source is None or not self._has_bitmap_streams:
            return None
        subtitle_streams = [
            s
            for s in (self._project.source.probe or {}).get("subtitle_streams", [])
            if isinstance(s, dict)
        ]
        settings = self.subtitle_settings()
        track = self._project.selected_track()
        stream_index = select_bitmap_subtitle_stream_index(
            subtitle_streams,
            preferred_index=settings.bitmap_stream_index,
            audio_language=track.language if track else None,
            ocr_only=True,
        )
        if stream_index is None:
            return None
        probe = probe_result_from_dict(
            {**dict(self._project.source.probe), "path": str(self._project.source.path)}
        )
        for stream in probe.subtitle_streams:
            if stream.index == stream_index:
                return self._format_bitmap_stream_label(stream)
        return f"stream #{stream_index}"

    def bitmap_convert_tracks(self) -> list[tuple[str, int]]:
        """OCR-capable bitmap tracks as ``(label, stream index)``."""
        if self._project is None or self._project.source is None:
            return []
        probe = probe_result_from_dict(
            {**dict(self._project.source.probe), "path": str(self._project.source.path)}
        )
        return [
            (self._format_bitmap_stream_label(stream), stream.index)
            for stream in probe.subtitle_streams
            if is_ocr_subtitle_codec(stream.codec)
        ]

    def prompt_bitmap_conversion(self, *, intro: str) -> BitmapConvertRequest | None:
        """Confirm OCR and return the chosen track. None means the user cancelled."""
        tracks = self.bitmap_convert_tracks()
        if not tracks:
            QMessageBox.information(
                self,
                "Convert bitmap subtitles",
                "No OCR-capable bitmap subtitle track is available in this file.",
            )
            return None
        dialog = BitmapConvertDialog(
            self,
            tracks=tracks,
            preferred_index=self.subtitle_settings().bitmap_stream_index,
            replace_existing=self._project is not None and self._project.has_ocr_subtitles(),
            intro=intro,
        )
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return None
        return BitmapConvertRequest(stream_index=dialog.selected_stream_index())

    def remember_bitmap_stream_index(self, index: int | None) -> None:
        """Store the track chosen for the next bitmap conversion."""
        self._cached_settings = replace(self._cached_settings, bitmap_stream_index=index)
        if self._project is not None:
            self._project.subtitle_settings.bitmap_stream_index = index
        self._sync_convert_bitmap_button()

    def _sync_convert_bitmap_button(self) -> None:
        has_project = self._project is not None
        enabled = not self._busy and has_project and self._has_bitmap_streams
        self._convert_bitmap_btn.setEnabled(enabled)
        if enabled:
            label = self.resolved_bitmap_stream_label()
            tooltip = _CONVERT_BITMAP_TOOLTIP
            if label:
                tooltip += f" Track: {label}."
            self._convert_bitmap_btn.setToolTip(tooltip)

    def subtitle_settings(self) -> SubtitleSettings:
        stream_data = self._sub_stream.currentData()
        current = self._cached_settings
        return SubtitleSettings(
            stream_index=int(stream_data) if stream_data is not None else None,
            bitmap_stream_index=current.bitmap_stream_index,
            append_generated=current.append_generated,
            append_edited=current.append_edited,
            replace_existing_generated_track=current.replace_existing_generated_track,
            replace_existing_edited_track=current.replace_existing_edited_track,
            track_name=current.track_name,
        )

    def _emit_subtitle_settings(self) -> None:
        if self._loading:
            return
        self._sync_convert_bitmap_button()
        self.subtitle_settings_changed.emit(self.subtitle_settings())

    def _populate_settings_controls(self, project: Project) -> None:
        if project.source is None:
            self._has_text_streams = False
            self._has_bitmap_streams = False
            self._sub_stream.setEnabled(False)
            self._cached_settings = project.subtitle_settings
            self._sync_convert_bitmap_button()
            return

        probe = probe_result_from_dict(
            {**dict(project.source.probe), "path": str(project.source.path)}
        )
        text_streams = [
            s for s in probe.subtitle_streams if is_text_subtitle_codec(s.codec)
        ]
        bitmap_streams = [
            s for s in probe.subtitle_streams if is_bitmap_subtitle_codec(s.codec)
        ]
        self._has_text_streams = bool(text_streams)
        self._has_bitmap_streams = bool(bitmap_streams)
        self._sub_stream.setEnabled(self._has_text_streams)

        self._sub_stream.blockSignals(True)
        self._sub_stream.clear()
        self._sub_stream.addItem("Auto (match audio language)", None)
        for stream in text_streams:
            self._sub_stream.addItem(
                self._format_bitmap_stream_label(stream),
                stream.index,
            )

        settings = project.subtitle_settings
        self._cached_settings = settings
        if settings.stream_index is not None:
            for row in range(self._sub_stream.count()):
                if self._sub_stream.itemData(row) == settings.stream_index:
                    self._sub_stream.setCurrentIndex(row)
                    break
            else:
                self._sub_stream.setCurrentIndex(0)
        else:
            self._sub_stream.setCurrentIndex(0)
        self._sub_stream.blockSignals(False)
        self._sync_convert_bitmap_button()

    def load_project(self, project: Project | None) -> None:
        self._loading = True
        self._playhead_cursor.reset()
        self._project = project
        self._profanity_lexicon = None
        try:
            if project is not None:
                self._populate_settings_controls(project)
                self._populate_cue_source_controls(project)
            else:
                self._has_text_streams = False
                self._has_bitmap_streams = False
                self._sub_stream.setEnabled(False)
                self._cached_settings = SubtitleSettings()
                self._sync_convert_bitmap_button()
                self._cue_source_row.setVisible(False)

            has_project = project is not None
            self._refresh_btn.setEnabled(has_project)
            self._generate_btn.setEnabled(has_project)
            self._settings_btn.setEnabled(has_project)
            self._add_cue_btn.setEnabled(has_project)
            self._cue_search.setEnabled(has_project)
            self._profanity_filter.setEnabled(has_project)
            if project is None:
                self.set_extract_status(None)
            if project is None or not project.subtitle_cues:
                self._cue_list.set_cues([])
                self._cue_list.setEnabled(False)
                self._reapply_ocr_btn.setEnabled(False)
                return

            self._reload_active_cue_table()
            self._generate_btn.setEnabled(True)
            self._refresh_btn.setEnabled(True)
            self._settings_btn.setEnabled(True)
            self._reapply_ocr_btn.setEnabled(project.has_ocr_subtitles())
        finally:
            self._loading = False
        self._apply_cue_filter()

    def _search_query(self) -> str:
        return self._cue_search.text().strip().casefold()

    def _profanity_lexicon_for_project(self) -> SwearLexicon:
        if self._profanity_lexicon is None:
            language: str | None = None
            if self._project is not None:
                track = self._project.selected_track()
                if track is not None:
                    language = track.language
            self._profanity_lexicon = SwearLexicon.build(track_language=language)
        return self._profanity_lexicon

    def _cue_has_profanity(self, cue: SubtitleCue) -> bool:
        lexicon = self._profanity_lexicon_for_project()
        return contains_profanity(cue.text, lexicon=lexicon) or contains_profanity(
            cue.edited_text,
            lexicon=lexicon,
        )

    def _cue_passes_filter(self, cue: SubtitleCue) -> bool:
        if self._profanity_filter.isChecked() and not self._cue_has_profanity(cue):
            return False
        query = self._search_query()
        return not query or any(
            query in field.casefold() for field in (cue.edited_text, cue.text)
        )

    def _filter_predicate(self) -> Callable[[SubtitleCue], bool] | None:
        if self._profanity_filter.isChecked() or self._search_query():
            return self._cue_passes_filter
        return None

    def _apply_cue_filter(self) -> None:
        if self._project is None:
            return
        self._cue_list.set_cues(
            self._project.subtitle_cues,
            predicate=self._filter_predicate(),
        )
        if self._cue_list.row_count() == 0 or self._cue_list.current_row() >= 0:
            return
        was_loading = self._loading
        self._loading = True
        try:
            self._cue_list.select_row(0, scroll=False)
        finally:
            self._loading = was_loading

    def select_cue(self, cue_id: str, *, scroll: bool = True) -> None:
        row = self._cue_list.row_for_id(cue_id)
        if row < 0:
            return
        self._cue_list.select_row(row, scroll=scroll)

    def follow_playhead(self, seconds: float) -> None:
        """Select the cue at ``seconds`` without seeking the timeline."""
        if self._project is None or not self._project.subtitle_cues:
            return
        cue = self._playhead_cursor.cue_at(self._project.subtitle_cues, seconds)
        if cue is None or not self._cue_passes_filter(cue):
            return
        row = self._cue_list.row_for_id(cue.id)
        if row >= 0 and self._cue_list.current_row() == row:
            return
        self._syncing_playhead = True
        try:
            self.select_cue(cue.id)
        finally:
            self._syncing_playhead = False

    def selected_cue(self) -> SubtitleCue | None:
        return self._selected_cue()

    def _selected_cue(self) -> SubtitleCue | None:
        if self._project is None:
            return None
        return self._cue_list.cue_at(self._cue_list.current_row())

    def _on_selection_changed(self, cue_id: str) -> None:
        if self._loading or self._syncing_playhead or not cue_id:
            return
        cue = self._selected_cue()
        if cue is None:
            return
        self.cue_selected.emit(cue.id)
        self.cue_seek.emit(float(cue.start.seconds))

    def _on_cue_activated(self, cue_id: str) -> None:
        if self._loading or not cue_id:
            return
        cue = self._cue_list.cue_at(self._cue_list.row_for_id(cue_id))
        if cue is None:
            return
        self.cue_seek.emit(float(cue.start.seconds))

    def _on_text_edited(self, cue_id: str, text: str) -> None:
        if self._loading or self._project is None:
            return
        self.cue_edited.emit(cue_id, text)

    def show_job_banner(self, heading: str) -> None:
        self._job_banner.start(heading)

    def set_job_progress(self, percent: int, message: str) -> None:
        self._job_banner.set_progress(percent, message)

    def note_job_detail(self, line: str) -> None:
        self._job_banner.set_detail(line)

    def mark_job_cancelling(self) -> None:
        self._job_banner.mark_cancelling()

    def hide_job_banner(self) -> None:
        self._job_banner.hide_job()

    def set_extract_status(self, message: str | None) -> None:
        """Show a non-blocking note while embedded cues are still loading."""
        text = (message or "").strip()
        self._extract_status.setText(text)
        self._extract_status.setVisible(bool(text))

    def set_busy(self, busy: bool) -> None:
        self._busy = busy
        has_project = self._project is not None
        self._refresh_btn.setEnabled(not busy and has_project)
        self._generate_btn.setEnabled(not busy and has_project)
        self._sync_convert_bitmap_button()
        self._settings_btn.setEnabled(not busy and has_project)
        self._cue_source.setEnabled(
            not busy and bool(self._project and self._project.available_subtitle_sources())
        )
        has_cues = self._project is not None and bool(self._project.subtitle_cues)
        self._reapply_ocr_btn.setEnabled(
            not busy and self._project is not None and self._project.has_ocr_subtitles()
        )
        self._add_cue_btn.setEnabled(not busy and self._project is not None)
        self._cue_search.setEnabled(not busy and self._project is not None)
        self._profanity_filter.setEnabled(not busy and self._project is not None)
        if has_cues:
            self._cue_list.setEnabled(not busy)

    def refresh_row_label(self, cue: SubtitleCue) -> None:
        self._loading = True
        try:
            self._cue_list.refresh_cue(cue, predicate=self._filter_predicate())
        finally:
            self._loading = False

    def refresh_selected_cue_fields(self) -> None:
        cue = self._selected_cue()
        if cue is None:
            return
        self.refresh_row_label(cue)
