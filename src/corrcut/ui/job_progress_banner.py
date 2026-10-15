"""Non-modal progress strip for long editor jobs."""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QHBoxLayout, QLabel, QProgressBar, QPushButton, QVBoxLayout, QWidget

from corrcut.ui.buttons import use_pill_button

_SKIP_LOG_PREFIXES = ("PROGRESS ", "RESULT ")


class JobProgressBanner(QWidget):
    """Compact dock banner: phase, percent, latest log line, and Cancel.

    The rest of the editor stays interactive while this is visible.
    """

    cancel_requested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("jobProgressBanner")
        self._heading = ""
        self._cancelling = False

        self._phase = QLabel("Working…")
        self._phase.setWordWrap(True)
        self._phase.setObjectName("jobProgressPhase")

        self._detail = QLabel()
        self._detail.setWordWrap(False)
        self._detail.setObjectName("jobProgressDetail")
        self._detail.setVisible(False)

        self._progress = QProgressBar()
        self._progress.setRange(0, 100)
        self._progress.setValue(0)
        self._progress.setTextVisible(False)
        self._progress.setFixedHeight(8)

        self._cancel_btn = QPushButton("Cancel")
        self._cancel_btn.setObjectName("jobProgressCancel")
        use_pill_button(self._cancel_btn)
        self._cancel_btn.clicked.connect(self._on_cancel_clicked)

        text_col = QVBoxLayout()
        text_col.setContentsMargins(0, 0, 0, 0)
        text_col.setSpacing(4)
        text_col.addWidget(self._phase)
        text_col.addWidget(self._detail)
        text_col.addWidget(self._progress)

        row = QHBoxLayout(self)
        row.setContentsMargins(8, 8, 8, 8)
        row.setSpacing(8)
        row.addLayout(text_col, stretch=1)
        row.addWidget(self._cancel_btn, alignment=Qt.AlignmentFlag.AlignVCenter)

        self.setStyleSheet(
            """
            QWidget#jobProgressBanner {
                background: rgba(255, 255, 255, 0.06);
                border: 1px solid rgba(255, 255, 255, 0.14);
                border-radius: 6px;
            }
            QLabel#jobProgressDetail {
                color: #9a9a9a;
            }
            """
        )
        self.hide()

    def start(self, heading: str) -> None:
        self._cancelling = False
        self._heading = heading.strip() or "Working…"
        self._phase.setText(self._heading)
        self._detail.clear()
        self._detail.setVisible(False)
        self._progress.setValue(0)
        self._cancel_btn.setEnabled(True)
        self.show()

    def set_progress(self, percent: int, message: str) -> None:
        if self._cancelling:
            return
        clamped = max(0, min(100, int(percent)))
        self._progress.setValue(clamped)
        text = message.strip() or self._heading
        self._phase.setText(f"{clamped}%  {text}" if text else f"{clamped}%")

    def set_detail(self, line: str) -> None:
        if self._cancelling:
            return
        text = " ".join(line.split())
        if not text or text.startswith(_SKIP_LOG_PREFIXES):
            return
        if len(text) > 140:
            text = text[:137] + "…"
        self._detail.setText(text)
        self._detail.setVisible(True)

    def mark_cancelling(self) -> None:
        if self._cancelling:
            return
        self._cancelling = True
        self._cancel_btn.setEnabled(False)
        self._phase.setText("Cancelling…")

    def hide_job(self) -> None:
        self._cancelling = False
        self.hide()

    def phase_text(self) -> str:
        return self._phase.text()

    def _on_cancel_clicked(self) -> None:
        if self._cancelling:
            return
        self.mark_cancelling()
        self.cancel_requested.emit()
