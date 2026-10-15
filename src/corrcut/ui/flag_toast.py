"""Brief on-screen confirmation when a timeline flag is dropped."""

from __future__ import annotations

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QLabel, QWidget


class FlagDropToast(QLabel):
    """Top-center chip so fullscreen watchers see that a flag was set."""

    _SHOW_MS = 1600

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.hide()
        self._hide_timer = QTimer(self)
        self._hide_timer.setSingleShot(True)
        self._hide_timer.timeout.connect(self.hide)

    def show_flag(self, host: QWidget, color: QColor, label: str, timecode: str) -> None:
        fill = QColor(color)
        fill.setAlpha(230)
        self.setParent(host)
        self.setText(f"  {label}  {timecode}  ")
        self.setStyleSheet(
            f"""
            QLabel {{
                background-color: rgba({fill.red()}, {fill.green()}, {fill.blue()}, 230);
                color: #111;
                font-size: 16px;
                font-weight: 600;
                padding: 8px 16px;
                border-radius: 6px;
            }}
            """
        )
        self.adjustSize()
        self.move(max(12, (host.width() - self.width()) // 2), 28)
        self.show()
        self.raise_()
        self._hide_timer.start(self._SHOW_MS)
