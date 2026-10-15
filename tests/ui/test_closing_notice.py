"""Quit shows a closing notice while background workers are still running."""

from __future__ import annotations

import threading

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

from corrcut.ai.detection_result import DetectionResult
from corrcut.config import APP_NAME
from corrcut.domain.dialogue_target import DialogueTargetStrategy
from corrcut.domain.project import Project, SourceFileRecord, TrackRecord
from corrcut.ui.closing_notice import ClosingNotice, closing_wait_detail
from corrcut.ui.editor_window import EditorWindow


def test_closing_wait_detail_names_running_jobs() -> None:
    assert closing_wait_detail([]) == "Stopping background work…"
    assert closing_wait_detail(["  ", ""]) == "Stopping background work…"
    assert closing_wait_detail(["AI detection"]) == "Stopping AI detection…"
    assert (
        closing_wait_detail(["Build waveform cache", "AI detection"])
        == "Stopping Build waveform cache and AI detection…"
    )
    assert (
        closing_wait_detail(["Waveform", "Filmstrip thumbnails", "Detect scene cuts"])
        == "Stopping Waveform, Filmstrip thumbnails, and Detect scene cuts…"
    )


def test_close_without_workers_skips_closing_notice(
    editor_window: EditorWindow,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(QApplication, "quit", lambda *args, **kwargs: None)
    editor_window.setAttribute(Qt.WidgetAttribute.WA_QuitOnClose, False)

    editor_window.close()

    assert editor_window.findChild(ClosingNotice) is None
    assert editor_window._closing_notice is None
    assert "Closing" not in editor_window.windowTitle()


def test_close_shows_notice_while_waiting_for_workers(
    editor_window: EditorWindow,
    qtbot,  # type: ignore[no-untyped-def]
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    started = threading.Event()
    release = threading.Event()

    def _work() -> None:
        started.set()
        release.wait(timeout=5)

    assert editor_window._waveform_job_runner.run_callable("Build waveform cache", _work)
    qtbot.waitUntil(started.is_set, timeout=3000)

    quit_calls = {"count": 0}

    def _quit(*_args: object, **_kwargs: object) -> None:
        quit_calls["count"] += 1

    monkeypatch.setattr(QApplication, "quit", _quit)
    editor_window.setAttribute(Qt.WidgetAttribute.WA_QuitOnClose, False)

    editor_window.close()

    notice = editor_window._closing_notice
    assert notice is not None
    assert notice.isVisible()
    assert notice.heading_text() == "Closing…"
    assert notice.detail_text() == "Stopping Build waveform cache…"
    assert editor_window.windowTitle() == f"{APP_NAME} — Closing…"
    assert not editor_window._quit_accepted
    assert quit_calls["count"] == 0

    release.set()
    qtbot.waitUntil(lambda: quit_calls["count"] >= 1, timeout=5000)
    assert editor_window._closing_notice is None


def test_quit_does_not_rewrite_project_files(
    editor_window: EditorWindow,
    tmp_path,
) -> None:
    project_path = tmp_path / "movie.corrcut"
    original = '{"keep": true}'
    project_path.write_text(original, encoding="utf-8")
    media = tmp_path / "movie.mkv"
    project = Project(
        source=SourceFileRecord(
            path=media,
            fingerprint={"test": True},
            probe={"duration_seconds": 10.0},
        ),
        tracks=[
            TrackRecord(
                stream_index=1,
                codec="aac",
                channel_layout="stereo",
                channels=2,
                sample_rate=48000,
                language="eng",
                dialogue_target_strategy=DialogueTargetStrategy.FULL_MIX,
            )
        ],
        selected_audio_stream_index=1,
        project_path=project_path,
    )
    editor_window._set_project(project)
    editor_window._quit_in_progress = True

    editor_window._save_project()
    editor_window._save_corr_json()
    editor_window._ai_job_project = project
    editor_window._on_ai_job_result(DetectionResult(suggestions=[]))

    assert project_path.read_text(encoding="utf-8") == original
    assert not (tmp_path / "movie.corr.json").exists()
    assert editor_window._ai_job_project is None
