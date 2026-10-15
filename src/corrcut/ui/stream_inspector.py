"""Media stream inspector: video, audio, and subtitles (Phase 1)."""

from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QLabel,
    QListWidget,
    QListWidgetItem,
    QVBoxLayout,
    QWidget,
)

from corrcut.ai.subtitles import is_text_subtitle_codec
from corrcut.domain.project import Project
from corrcut.engine.probe import MediaProbeResult, probe_result_from_dict


class StreamInspector(QWidget):
    track_selected = Signal(int)

    def __init__(self) -> None:
        super().__init__()
        layout = QVBoxLayout(self)

        self._file_label = QLabel()
        self._file_label.setWordWrap(True)
        layout.addWidget(self._file_label)

        layout.addWidget(QLabel("<b>Video</b>"))
        self._video_list = QListWidget()
        self._video_list.setMaximumHeight(72)
        layout.addWidget(self._video_list)

        layout.addWidget(QLabel("<b>Audio tracks</b>"))
        self._audio_list = QListWidget()
        layout.addWidget(self._audio_list)

        layout.addWidget(QLabel("<b>Subtitles</b>"))
        self._subtitle_list = QListWidget()
        self._subtitle_list.setMaximumHeight(108)
        layout.addWidget(self._subtitle_list)

        self._audio_list.currentRowChanged.connect(self._on_audio_row_changed)
        self._loading = False

    def load_project(self, project: Project) -> None:
        self._loading = True
        self._audio_list.blockSignals(True)
        try:
            self._video_list.clear()
            self._audio_list.clear()
            self._subtitle_list.clear()

            if project.source is None:
                self._file_label.setText("No media loaded")
                return

            if project.source.path.is_file():
                self._file_label.setText(f"<b>{project.source.path.name}</b>")
            else:
                self._file_label.setText(
                    f"<b>{project.source.path.name}</b> "
                    "<span style='color:#c44'>(missing — File → Relink media)</span>"
                )
            probe = self._probe_from_project(project)

            for v in probe.video_streams:
                res = f"{v.width}×{v.height}" if v.width and v.height else "?"
                rate = f", {v.frame_rate}" if v.frame_rate else ""
                self._video_list.addItem(
                    QListWidgetItem(f"#{v.index} {v.codec} {res}{rate}")
                )
            if self._video_list.count() == 0:
                self._video_list.addItem(QListWidgetItem("(none)"))

            for track in project.tracks:
                item = QListWidgetItem(track.list_label())
                item.setData(256, track.stream_index)
                self._audio_list.addItem(item)

            for s in probe.subtitle_streams:
                lang = f" ({s.language})" if s.language else ""
                title = f' — {s.title}' if s.title else ""
                bitmap = "" if is_text_subtitle_codec(s.codec) else " [bitmap]"
                self._subtitle_list.addItem(
                    QListWidgetItem(f"#{s.index} {s.codec}{lang}{title}{bitmap}")
                )
            if self._subtitle_list.count() == 0:
                self._subtitle_list.addItem(QListWidgetItem("(none)"))

            if project.selected_audio_stream_index is not None:
                for row in range(self._audio_list.count()):
                    if self._audio_list.item(row).data(256) == project.selected_audio_stream_index:
                        self._audio_list.setCurrentRow(row)
                        break
        finally:
            self._audio_list.blockSignals(False)
            self._loading = False

    def _probe_from_project(self, project: Project) -> MediaProbeResult:
        assert project.source is not None
        data = dict(project.source.probe)
        data["path"] = str(project.source.path)
        return probe_result_from_dict(data)

    def select_audio_stream(self, stream_index: int) -> None:
        """Highlight *stream_index* without emitting ``track_selected``."""
        self._loading = True
        self._audio_list.blockSignals(True)
        try:
            for row in range(self._audio_list.count()):
                if self._audio_list.item(row).data(256) == stream_index:
                    self._audio_list.setCurrentRow(row)
                    return
        finally:
            self._audio_list.blockSignals(False)
            self._loading = False

    def _on_audio_row_changed(self, row: int) -> None:
        if row < 0:
            return
        item = self._audio_list.item(row)
        if item is None:
            return
        stream_index = item.data(256)
        if stream_index is None:
            return
        self.track_selected.emit(int(stream_index))
