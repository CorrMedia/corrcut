"""Shared pytest fixtures."""

from __future__ import annotations

import pytest
from PySide6.QtWidgets import QApplication, QMessageBox

from corrcut.config import AppPaths
from corrcut.ui.editor_window import EditorWindow

_app = QApplication.instance() or QApplication([])


def _discard_message_box(*_args: object, **_kwargs: object) -> QMessageBox.StandardButton:
    return QMessageBox.StandardButton.Discard


@pytest.fixture
def editor_window(qtbot, monkeypatch: pytest.MonkeyPatch) -> EditorWindow:  # type: ignore[no-untyped-def]
    """Editor shell with job runners shut down after each test."""
    monkeypatch.setattr(
        "corrcut.preview.mpv_backend.is_mpv_available",
        lambda: False,
    )
    monkeypatch.setattr(
        "corrcut.ui.editor.project_session.QMessageBox.warning",
        _discard_message_box,
    )
    window = EditorWindow(AppPaths.default())
    qtbot.addWidget(window)
    yield window
    if window._project is not None:
        window._mark_project_saved()
        window._set_project(None)
    window._waveform_job_runner.shutdown()
    window._ai_job_runner.shutdown()
    window._subtitle_job_runner.shutdown()
    window._filmstrip_job_runner.shutdown()
    window._scene_cut_job_runner.shutdown()
    window._job_runner.shutdown()
    window._media_open_job_runner.shutdown()
