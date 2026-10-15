"""Regression: Close project after Open media must not crash and must reset UI."""

from __future__ import annotations

from pathlib import Path

import pytest

from corrcut.domain.dialogue_target import DialogueTargetStrategy
from corrcut.domain.project import Project, SourceFileRecord, TrackRecord
from corrcut.preview.mpv_backend import is_mpv_available
from corrcut.ui.editor_window import EditorWindow
from corrcut.ui.preview_panel import PreviewPanel


def _loaded_project(*, media_path: Path | None = None) -> Project:
    tracks = [
        TrackRecord(
            stream_index=1,
            codec="aac",
            channel_layout="stereo",
            channels=2,
            sample_rate=48000,
            language="eng",
            dialogue_target_strategy=DialogueTargetStrategy.FULL_MIX,
        ),
    ]
    path = media_path or Path(__file__).with_name("missing_editor_fixture.mkv")
    return Project(
        source=SourceFileRecord(
            path=path,
            fingerprint={"test": True},
            probe={"duration_seconds": 120.0, "subtitle_streams": []},
        ),
        tracks=tracks,
        selected_audio_stream_index=1,
        operations=[],
    )


def test_preview_reset_to_idle_after_media(qtbot, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    media = tmp_path / "clip.mkv"
    media.write_bytes(b"\x00")
    panel = PreviewPanel()
    qtbot.addWidget(panel)
    panel.set_project(_loaded_project(media_path=media))
    assert panel._media_path is not None

    panel.reset_to_idle()

    assert panel._project is None
    assert panel._media_path is None
    if is_mpv_available():
        assert panel.mpv_available


def test_editor_switch_project_replaces_session(editor_window: EditorWindow) -> None:
    first = _loaded_project()
    second = _loaded_project()
    second.source.probe["duration_seconds"] = 45.0  # type: ignore[index]
    second.source.path = Path(__file__).with_name("other.mkv")

    editor_window._set_project(first)
    editor_window._prepare_replace_session()
    editor_window._set_project(second)

    assert editor_window._project is second
    assert editor_window._duration == pytest.approx(45.0)
    assert editor_window._preview_panel._project is second


def test_editor_close_project_clears_session(editor_window: EditorWindow) -> None:
    editor_window._set_project(_loaded_project())
    assert editor_window._project is not None
    assert editor_window._duration == pytest.approx(120.0)

    editor_window._close_project()

    assert editor_window._project is None
    assert editor_window._peak_store is None
    timeline = editor_window._timeline
    assert timeline._content_width() == pytest.approx(timeline.viewport().width(), abs=1.0)  # noqa: SLF001
    assert editor_window._duration == pytest.approx(timeline._duration)  # noqa: SLF001
    assert timeline.horizontalScrollBar().maximum() == 0
    assert editor_window._preview_panel._media_path is None
    assert editor_window._duration_label.text() == "Length: 00:00:00.000"
