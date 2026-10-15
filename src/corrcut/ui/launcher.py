"""Application shell — welcome screen then editor."""

from __future__ import annotations

import locale
import sys
from pathlib import Path

from PySide6.QtWidgets import QApplication

from corrcut.config import AppPaths
from corrcut.ui.buttons import install_pill_buttons
from corrcut.ui.editor_window import EditorWindow
from corrcut.ui.icons import install_window_icons
from corrcut.ui.square_menus import install_square_menu_corners
from corrcut.ui.welcome_window import WelcomeWindow


class ApplicationShell:
    def __init__(self, paths: AppPaths) -> None:
        self._paths = paths
        self._welcome = WelcomeWindow(paths.cache_dir)
        self._editor: EditorWindow | None = None

        self._welcome.new_project_requested.connect(self._start_new_project)
        self._welcome.open_project_requested.connect(self._open_project_from_dialog)
        self._welcome.recent_project_requested.connect(self._open_recent_project)

    def show(self) -> None:
        self._welcome.show()

    def _show_editor(self) -> None:
        self._welcome.hide()
        if self._editor is None:
            self._editor = EditorWindow(self._paths)
        self._editor.showMaximized()
        self._editor.raise_()
        self._editor.activateWindow()

    def _start_new_project(self) -> None:
        self._show_editor()
        assert self._editor is not None
        self._editor.start_new_project(prompt_open_media=True)

    def _open_project_from_dialog(self) -> None:
        path = self._welcome.pick_project_file()
        if path is None:
            return
        self._open_recent_project(path)

    def _open_recent_project(self, path: Path) -> None:
        self._show_editor()
        assert self._editor is not None
        self._editor.load_project_file(path)


def run_application(paths: AppPaths | None = None) -> int:
    locale.setlocale(locale.LC_NUMERIC, "C")
    app = QApplication(sys.argv)
    install_window_icons(app)
    install_square_menu_corners(app)
    install_pill_buttons(app)
    shell = ApplicationShell(paths or AppPaths.default())
    shell.show()
    return app.exec()
