"""Preview remount after Windows modal file dialogs."""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from PySide6.QtCore import QEventLoop, QTimer
from PySide6.QtWidgets import QApplication

from corrcut.domain.dialogue_target import DialogueTargetStrategy
from corrcut.domain.project import Project, SourceFileRecord, TrackRecord
from corrcut.ui.preview_panel import PreviewPanel, _mpv_embed_widget

_app = QApplication.instance() or QApplication([])


def _mock_preview_backend() -> MagicMock:
    backend = MagicMock()
    backend.video_letterbox_rect.return_value = None
    return backend


def _sample_project(media_path: Path) -> Project:
    return Project(
        source=SourceFileRecord(
            path=media_path,
            fingerprint={},
            probe={"duration_seconds": 30.0, "subtitle_streams": []},
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
            ),
        ],
        selected_audio_stream_index=1,
    )


def test_restore_after_modal_remounts_mpv_on_windows(monkeypatch: pytest.MonkeyPatch) -> None:
    """Save/open dialogs unmount mpv on Windows; restore must bring playback back."""
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(
        "corrcut.ui.preview_panel.is_mpv_available",
        lambda: True,
    )
    panel = PreviewPanel()
    media_path = Path(__file__)
    panel._project = _sample_project(media_path)
    panel._media_path = media_path

    mount_calls: list[object] = []
    reload_calls: list[object] = []

    def fake_mount() -> None:
        mount_calls.append(True)
        panel._backend = _mock_preview_backend()
        panel._mpv_widget = MagicMock()

    panel._mount_video_widget = fake_mount  # type: ignore[method-assign]
    panel._reload_media_with_filters = lambda: reload_calls.append(True)  # type: ignore[method-assign]

    panel.unmount_mpv_embed()
    assert _mpv_embed_widget(panel._mpv_widget) is None

    panel.restore_after_modal_dialog()

    assert mount_calls == [True]
    assert reload_calls == [True]
    panel.close()


def test_reload_media_with_filters_wires_duration_handler() -> None:
    panel = PreviewPanel()
    media_path = Path(__file__)
    panel._project = _sample_project(media_path)
    panel._media_path = media_path

    fake_backend = _mock_preview_backend()
    panel._backend = fake_backend
    panel._reload_media_with_filters()

    loop = QEventLoop()
    QTimer.singleShot(0, loop.quit)
    loop.exec()

    fake_backend.duration_changed.connect.assert_called_once()
    fake_backend.load.assert_called_once_with(media_path)
    panel.close()
