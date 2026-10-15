"""Modal job progress with cancel."""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from corrcut.ui.buttons import use_pill_button


class ExportProgressDialog(QDialog):
    """Modal dialog shown while an export job runs on JobRunner."""

    cancel_requested = Signal()

    def __init__(
        self,
        heading: str,
        *,
        window_title: str = "Working…",
        status: str = "Starting export…",
        log_placeholder: str = "Export log…",
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._finished = False
        self._cancelling = False

        self.setWindowTitle(window_title)
        self.setModal(True)
        self.setMinimumWidth(460)
        self.setWindowFlag(Qt.WindowType.WindowCloseButtonHint, False)

        heading_label = QLabel(heading)
        heading_label.setWordWrap(True)

        self._phase = QLabel(status)
        self._progress = QProgressBar()
        self._progress.setRange(0, 100)
        self._progress.setValue(0)

        self._log = QTextEdit()
        self._log.setReadOnly(True)
        self._log.setMaximumHeight(120)
        self._log.setPlaceholderText(log_placeholder)

        self._cancel_btn = QPushButton("Cancel")
        use_pill_button(self._cancel_btn)
        self._cancel_btn.clicked.connect(self._on_cancel_clicked)

        btn_row = QHBoxLayout()
        btn_row.addStretch()
        btn_row.addWidget(self._cancel_btn)

        layout = QVBoxLayout(self)
        layout.addWidget(heading_label)
        layout.addWidget(self._phase)
        layout.addWidget(self._progress)
        layout.addWidget(self._log)
        layout.addLayout(btn_row)

    def set_progress(self, percent: int, message: str) -> None:
        self._progress.setValue(max(0, min(100, percent)))
        if message:
            self._phase.setText(message)

    def append_log(self, line: str) -> None:
        if not line:
            return
        self._log.append(line)
        sb = self._log.verticalScrollBar()
        sb.setValue(sb.maximum())

    def set_cancel_enabled(self, enabled: bool) -> None:
        if self._finished or self._cancelling:
            return
        self._cancel_btn.setEnabled(enabled)

    def mark_cancelling(self) -> None:
        self._cancelling = True
        self._cancel_btn.setEnabled(False)
        self._phase.setText("Cancelling…")

    def mark_finished(self) -> None:
        self._finished = True
        self._cancel_btn.setEnabled(False)
        self._progress.setValue(100)
        self._phase.setText("Finished")

    @property
    def is_cancelling(self) -> bool:
        return self._cancelling

    def _on_cancel_clicked(self) -> None:
        if self._finished or self._cancelling:
            return
        self.mark_cancelling()
        self.cancel_requested.emit()

    def closeEvent(self, event) -> None:  # type: ignore[no-untyped-def]  # noqa: N802
        if not self._finished:
            event.ignore()
            if not self._cancelling:
                self._on_cancel_clicked()
            return
        super().closeEvent(event)
