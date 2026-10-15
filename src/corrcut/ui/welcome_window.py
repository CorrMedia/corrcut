"""Launch screen — recent projects and new/open project actions."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QPoint, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QIcon, QImage, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from corrcut.config import APP_NAME, AppPaths
from corrcut.io.project_store import PROJECT_SUFFIX
from corrcut.io.recent_posters import POSTER_HEIGHT, POSTER_WIDTH, poster_cache_path
from corrcut.io.recent_projects import (
    RecentProjectEntry,
    list_recent_projects,
    remove_recent_project,
)
from corrcut.notices import use_notice
from corrcut.ui.buttons import use_pill_button
from corrcut.ui.square_menus import RECENT_PROJECT_LIST_PROPERTY
from corrcut.ui.theme import apply_muted_text, choose_open_path

_POSTER_RADIUS = 6.0


class WelcomeWindow(QMainWindow):
    new_project_requested = Signal()
    open_project_requested = Signal()
    recent_project_requested = Signal(object)

    def __init__(self, cache_dir: Path | None = None) -> None:
        super().__init__()
        self._poster_cache = cache_dir if cache_dir is not None else AppPaths.default().cache_dir
        self._placeholder_icon = _placeholder_poster()
        self.setWindowTitle(APP_NAME)
        self.resize(720, 480)

        central = QWidget()
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)
        layout.setContentsMargins(32, 32, 32, 32)
        layout.setSpacing(16)

        title = QLabel(APP_NAME)
        title.setStyleSheet("font-size: 24px; font-weight: 600;")
        layout.addWidget(title)

        self._recent_list = QListWidget()
        self._recent_list.setProperty(RECENT_PROJECT_LIST_PROPERTY, True)
        self._recent_list.setIconSize(QSize(POSTER_WIDTH, POSTER_HEIGHT))
        self._recent_list.setSpacing(4)
        self._recent_list.setWordWrap(False)
        self._recent_list.setTextElideMode(Qt.TextElideMode.ElideRight)
        self._recent_list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._recent_list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._recent_list.customContextMenuRequested.connect(self._on_recent_context_menu)
        self._recent_list.itemDoubleClicked.connect(self._on_recent_double_clicked)
        layout.addWidget(self._recent_list, stretch=1)

        self._empty_label = QLabel("No recent projects yet.")
        apply_muted_text(self._empty_label, extra_css="font-style: italic;")
        layout.addWidget(self._empty_label)

        action_row = QHBoxLayout()
        action_row.setSpacing(8)
        self._new_btn = QPushButton("New project")
        use_pill_button(self._new_btn)
        self._new_btn.clicked.connect(self.new_project_requested.emit)
        self._open_btn = QPushButton("Browse for a project…")
        use_pill_button(self._open_btn)
        self._open_btn.clicked.connect(self.open_project_requested.emit)
        action_row.addStretch(1)
        action_row.addWidget(self._new_btn)
        action_row.addWidget(self._open_btn)
        layout.addLayout(action_row)

        self._use_notice = QLabel(use_notice())
        self._use_notice.setWordWrap(True)
        apply_muted_text(self._use_notice)
        layout.addWidget(self._use_notice)

        self.refresh_recents()

    def closeEvent(self, event) -> None:  # type: ignore[no-untyped-def]
        app = QApplication.instance()
        if app is not None:
            app.quit()
        super().closeEvent(event)

    def refresh_recents(self) -> None:
        self._recent_list.clear()
        entries = list_recent_projects()
        for entry in entries:
            item = QListWidgetItem(self._poster_icon(entry.path), self._row_title(entry))
            item.setData(Qt.ItemDataRole.UserRole, entry.path)
            item.setToolTip(self._row_tooltip(entry))
            if not entry.exists:
                item.setForeground(Qt.GlobalColor.gray)
            self._recent_list.addItem(item)
            hint = item.sizeHint()
            item.setSizeHint(QSize(hint.width(), max(hint.height(), POSTER_HEIGHT + 16)))
        has_entries = bool(entries)
        self._recent_list.setVisible(has_entries)
        self._empty_label.setVisible(not has_entries)

    def _row_title(self, entry: RecentProjectEntry) -> str:
        if entry.path.suffix.lower() == PROJECT_SUFFIX:
            return entry.path.stem
        return entry.path.name

    def _row_tooltip(self, entry: RecentProjectEntry) -> str:
        if entry.exists:
            return str(entry.path)
        return f"{entry.path}\nFile not found"

    def _poster_icon(self, project_path: Path) -> QIcon:
        path = poster_cache_path(self._poster_cache, project_path)
        if path.is_file():
            pixmap = QPixmap(str(path))
            if not pixmap.isNull():
                return QIcon(_rounded_poster(pixmap))
        return self._placeholder_icon

    def _selected_entry_path(self) -> Path | None:
        item = self._recent_list.currentItem()
        if item is None:
            return None
        data = item.data(Qt.ItemDataRole.UserRole)
        if data is None:
            return None
        return Path(str(data))

    def _on_recent_context_menu(self, pos: QPoint) -> None:
        item = self._recent_list.itemAt(pos)
        if item is None:
            return
        self._recent_list.setCurrentItem(item)
        path = self._selected_entry_path()
        if path is None:
            return
        menu = self._recent_context_menu(path)
        menu.exec(self._recent_list.viewport().mapToGlobal(pos))

    def _recent_context_menu(self, path: Path) -> QMenu:
        menu = QMenu(self)
        open_act = menu.addAction("Open")
        open_act.triggered.connect(
            lambda _checked=False, target=path: self._emit_recent_path(target)
        )
        remove_act = menu.addAction("Remove from list")
        remove_act.triggered.connect(
            lambda _checked=False, target=path: self._forget_recent(target)
        )
        return menu

    def _forget_recent(self, path: Path) -> None:
        remove_recent_project(path)
        self.refresh_recents()

    def _on_recent_double_clicked(self, item: QListWidgetItem) -> None:
        data = item.data(Qt.ItemDataRole.UserRole)
        if data is None:
            return
        self._emit_recent_path(Path(str(data)))

    def _emit_recent_path(self, path: Path) -> None:
        if not path.is_file():
            reply = QMessageBox.warning(
                self,
                "Project not found",
                f"The project file no longer exists:\n{path}\n\n"
                "Remove it from recent projects?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.Yes,
            )
            if reply == QMessageBox.StandardButton.Yes:
                remove_recent_project(path)
                self.refresh_recents()
            return
        self.recent_project_requested.emit(path)

    def pick_project_file(self) -> Path | None:
        return choose_open_path(
            self,
            "Open project",
            f"{APP_NAME} project (*{PROJECT_SUFFIX});;All (*.*)",
        )


def _poster_clip() -> QPainterPath:
    path = QPainterPath()
    path.addRoundedRect(QRectF(0, 0, POSTER_WIDTH, POSTER_HEIGHT), _POSTER_RADIUS, _POSTER_RADIUS)
    return path


def _rounded_poster(pixmap: QPixmap) -> QPixmap:
    scaled = pixmap.scaled(
        POSTER_WIDTH,
        POSTER_HEIGHT,
        Qt.AspectRatioMode.KeepAspectRatioByExpanding,
        Qt.TransformationMode.SmoothTransformation,
    )
    x = max(0, (scaled.width() - POSTER_WIDTH) // 2)
    y = max(0, (scaled.height() - POSTER_HEIGHT) // 2)
    image = QImage(POSTER_WIDTH, POSTER_HEIGHT, QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(Qt.GlobalColor.transparent)
    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    painter.setClipPath(_poster_clip())
    painter.drawPixmap(0, 0, scaled, x, y, POSTER_WIDTH, POSTER_HEIGHT)
    painter.end()
    return QPixmap.fromImage(image)


def _placeholder_poster() -> QIcon:
    image = QImage(POSTER_WIDTH, POSTER_HEIGHT, QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(Qt.GlobalColor.transparent)
    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QColor("#2a2a2a"))
    painter.drawPath(_poster_clip())
    painter.setPen(QPen(QColor("#3a3a3a")))
    painter.setBrush(Qt.BrushStyle.NoBrush)
    painter.drawPath(_poster_clip())
    painter.end()
    return QIcon(QPixmap.fromImage(image))
