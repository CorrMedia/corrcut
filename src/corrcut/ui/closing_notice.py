"""Modal notice shown while quit waits for background work."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QDialog, QLabel, QProgressBar, QVBoxLayout, QWidget

from corrcut.config import APP_NAME


def closing_wait_detail(labels: list[str]) -> str:
    """Explain which cancelled background work quit is waiting to stop."""
    names = [label.strip() for label in labels if label.strip()]
    if not names:
        return "Stopping background work…"
    if len(names) == 1:
        return f"Stopping {names[0]}…"
    if len(names) == 2:
        return f"Stopping {names[0]} and {names[1]}…"
    listed = ", ".join(names[:-1])
    return f"Stopping {listed}, and {names[-1]}…"


class ClosingNotice(QDialog):
    """Tell the user the application is closing while workers stop."""

    def __init__(self, parent: QWidget | None = None) -> None:
        # Top-level window so it paints even while the editor is freezing out.
        super().__init__(None)
        self.setObjectName("closingNotice")
        self.setWindowTitle(f"Closing {APP_NAME}")
        self.setModal(True)
        self.setMinimumWidth(420)
        self.setMinimumHeight(140)
        self.setWindowFlags(
            Qt.WindowType.Dialog
            | Qt.WindowType.CustomizeWindowHint
            | Qt.WindowType.WindowTitleHint
            | Qt.WindowType.WindowStaysOnTopHint
        )
        self.setWindowModality(Qt.WindowModality.ApplicationModal)
        self.setStyleSheet("""
            QDialog#closingNotice {
                background: #2b2b2b;
                color: #f0f0f0;
            }
            QLabel#closingHeading {
                color: #f0f0f0;
                font-weight: 600;
                font-size: 16px;
            }
            QLabel#closingDetail {
                color: #b0b0b0;
            }
            QProgressBar {
                border: 1px solid #555;
                border-radius: 3px;
                background: #1e1e1e;
                min-height: 10px;
                max-height: 10px;
            }
            QProgressBar::chunk {
                background: #6a9fd8;
            }
            """)

        self._heading = QLabel("Closing…")
        self._heading.setObjectName("closingHeading")

        self._detail = QLabel("Stopping background work…")
        self._detail.setObjectName("closingDetail")
        self._detail.setWordWrap(True)

        progress = QProgressBar()
        progress.setRange(0, 0)
        progress.setTextVisible(False)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(12)
        layout.addWidget(self._heading)
        layout.addWidget(self._detail)
        layout.addWidget(progress)

        if parent is not None:
            geo = parent.frameGeometry()
            self.adjustSize()
            self.move(
                geo.center().x() - self.width() // 2,
                geo.center().y() - self.height() // 2,
            )
        else:
            screen = QGuiApplication.primaryScreen()
            if screen is not None:
                geo = screen.availableGeometry()
                self.adjustSize()
                self.move(
                    geo.center().x() - self.width() // 2,
                    geo.center().y() - self.height() // 2,
                )

    def set_detail(self, text: str) -> None:
        self._detail.setText(text)

    def heading_text(self) -> str:
        return self._heading.text()

    def detail_text(self) -> str:
        return self._detail.text()

    def paint_now(self) -> None:
        """Force the first frame onto the screen before a long wait."""
        self.show()
        self.raise_()
        self.activateWindow()
        self.repaint()
