"""Confirm bitmap-subtitle OCR and choose the source track."""

from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLabel,
    QVBoxLayout,
    QWidget,
)


@dataclass(frozen=True)
class BitmapConvertRequest:
    """Confirmed OCR conversion. ``stream_index`` None selects the track automatically."""

    stream_index: int | None


class BitmapConvertDialog(QDialog):
    """Ask before OCR. A track combo appears only when several bitmap tracks exist."""

    def __init__(
        self,
        parent: QWidget | None,
        *,
        tracks: list[tuple[str, int]],
        preferred_index: int | None,
        replace_existing: bool,
        intro: str,
    ) -> None:
        if not tracks:
            raise ValueError("BitmapConvertDialog requires at least one track")
        super().__init__(parent)
        self._tracks = list(tracks)
        self._track: QComboBox | None = None
        self.setWindowTitle("Convert bitmap subtitles")

        layout = QVBoxLayout(self)
        intro_label = QLabel(intro)
        intro_label.setWordWrap(True)
        layout.addWidget(intro_label)

        if len(self._tracks) == 1:
            track_label = QLabel(f"Track: {self._tracks[0][0]}")
            track_label.setWordWrap(True)
            layout.addWidget(track_label)
        else:
            combo = QComboBox()
            combo.addItem("Auto (match audio language)", None)
            selected = 0
            for label, index in self._tracks:
                combo.addItem(label, index)
                if preferred_index is not None and index == preferred_index:
                    selected = combo.count() - 1
            combo.setCurrentIndex(selected)
            form = QFormLayout()
            form.addRow("Bitmap track", combo)
            layout.addLayout(form)
            self._track = combo

        if replace_existing:
            replace_label = QLabel("This replaces the existing Bitmap OCR cues.")
            replace_label.setWordWrap(True)
            layout.addWidget(replace_label)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        convert = buttons.button(QDialogButtonBox.StandardButton.Ok)
        if convert is not None:
            convert.setText("Convert")
            convert.setDefault(True)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def selected_stream_index(self) -> int | None:
        """Stream to OCR, or None to match the audio language automatically."""
        if len(self._tracks) == 1:
            return self._tracks[0][1]
        track = self._track
        if track is None:
            return None
        data = track.currentData()
        if data is None:
            return None
        return int(data)
