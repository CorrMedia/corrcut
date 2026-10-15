"""Long Whisper jobs report progress without a modal dialog."""

from __future__ import annotations

from fractions import Fraction
from pathlib import Path

import pytest
from PySide6.QtWidgets import QApplication, QDialog, QMessageBox

from corrcut.domain.ai_suggestion import AiSuggestion, DetectionSettings
from corrcut.domain.dialogue_target import DialogueTargetStrategy
from corrcut.domain.operation import TimeRange
from corrcut.domain.project import Project, SourceFileRecord, TrackRecord
from corrcut.domain.time_point import TimePoint
from corrcut.engine.jobs.cancel import JobCancelled
from corrcut.ui.editor_window import EditorWindow


class _Tool:
    name = "ffmpeg"
    ok = True


class _Toolchain:
    tools = (_Tool(),)


def _project(media: Path) -> Project:
    return Project(
        source=SourceFileRecord(
            path=media,
            fingerprint={"size": 1, "head_sha256": "a", "tail_sha256": ""},
            probe={"duration_seconds": 12.0},
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
        ai_suggestions=[
            AiSuggestion.create(
                stream_index=1,
                time_range=TimeRange(
                    start=TimePoint(Fraction("0.4")),
                    end=TimePoint(Fraction("0.8")),
                ),
                matched_word="damn",
                matched_term="damn",
                transcript_phrase="damn",
                confidence=0.9,
                whisper_probability=0.9,
                alignment_quality="good",
            )
        ],
    )


def _block_until_cancel(**kwargs: object) -> None:
    progress = kwargs.get("progress")
    if callable(progress):
        progress(18, "Transcribing…")
    cancel = kwargs.get("cancel_event")
    if cancel is not None and hasattr(cancel, "wait"):
        cancel.wait(5)
    raise JobCancelled()


def _assert_editor_stays_interactive(editor_window: EditorWindow) -> None:
    assert editor_window.isEnabled()
    assert editor_window._timeline.isEnabled()
    assert editor_window._preview_panel.isEnabled()
    assert QApplication.activeModalWidget() is None
    assert not any(isinstance(widget, QDialog) and widget.isVisible() for widget in editor_window.findChildren(QDialog))


def test_ai_detection_runs_without_blocking_review(
    editor_window: EditorWindow,
    qtbot,  # type: ignore[no-untyped-def]
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    media = tmp_path / "clip.mkv"
    media.write_bytes(b"x")
    editor_window._project = _project(media)
    editor_window._duration = 12.0
    editor_window._timeline.set_duration(12.0)
    editor_window._suggestions_panel.load_suggestions(editor_window._project.ai_suggestions)
    monkeypatch.setattr("corrcut.ui.editor.ai_job.is_whisper_available", lambda: True)
    monkeypatch.setattr("corrcut.ui.editor.ai_job.check_toolchain", lambda: _Toolchain())
    monkeypatch.setattr(
        "corrcut.ui.editor.ai_job.run_detection",
        lambda *_args, **kwargs: _block_until_cancel(**kwargs),
    )

    editor_window._generate_suggestions(DetectionSettings())
    qtbot.waitUntil(
        lambda: "Transcribing" in editor_window._suggestions_panel._job_banner.phase_text(),
        timeout=3000,
    )

    panel = editor_window._suggestions_panel
    assert not panel._job_banner.isHidden()
    assert not panel._generate_btn.isEnabled()
    assert panel._table.isEnabled()
    assert panel._filter_btn.isEnabled()
    assert not editor_window._background_cancel_btn.isHidden()
    assert "Transcribing" in editor_window._status.currentMessage()
    _assert_editor_stays_interactive(editor_window)

    panel._table.cellClicked.emit(0, panel._COL_TIME)
    assert editor_window._timeline.playhead_seconds() == pytest.approx(0.4)

    panel._job_banner._cancel_btn.click()
    qtbot.waitUntil(lambda: not editor_window._ai_job_runner.is_busy, timeout=3000)
    assert panel._job_banner.isHidden()
    assert panel._generate_btn.isEnabled()
    assert "cancelled" in editor_window._status.currentMessage()


def test_whisper_subtitles_run_without_a_modal(
    editor_window: EditorWindow,
    qtbot,  # type: ignore[no-untyped-def]
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    media = tmp_path / "clip.mkv"
    media.write_bytes(b"x")
    editor_window._project = _project(media)
    monkeypatch.setattr("corrcut.ui.editor.subtitles.is_whisper_available", lambda: True)
    monkeypatch.setattr("corrcut.ui.editor.subtitles.check_toolchain", lambda: _Toolchain())
    monkeypatch.setattr(
        "corrcut.ui.editor.subtitles.transcribe_project_track",
        lambda *_args, **kwargs: _block_until_cancel(**kwargs),
    )

    editor_window._start_whisper_subtitle_generation()
    qtbot.waitUntil(
        lambda: "Transcribing" in editor_window._subtitles_panel._job_banner.phase_text(),
        timeout=3000,
    )

    banner = editor_window._subtitles_panel._job_banner
    assert not banner.isHidden()
    assert not editor_window._background_cancel_btn.isHidden()
    _assert_editor_stays_interactive(editor_window)

    banner._cancel_btn.click()
    qtbot.waitUntil(lambda: not editor_window._subtitle_job_runner.is_busy, timeout=3000)
    assert banner.isHidden()
    assert "cancelled" in editor_window._status.currentMessage()


def _ocr_project(media: Path) -> Project:
    project = _project(media)
    assert project.source is not None
    project.source.probe = {
        "video_streams": [{"index": 0}],
        "subtitle_streams": [
            {"index": 2, "codec": "hdmv_pgs_subtitle", "language": "eng"}
        ],
        "duration_seconds": 12.0,
    }
    return project


def _block_on_cancel(*_args: object, **kwargs: object) -> None:
    _block_until_cancel(**kwargs)


def test_bitmap_ocr_starts_while_subtitle_extract_runs(
    editor_window: EditorWindow,
    qtbot,  # type: ignore[no-untyped-def]
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    media = tmp_path / "clip.mkv"
    media.write_bytes(b"x")
    editor_window._project = _ocr_project(media)
    monkeypatch.setattr(
        "corrcut.ui.editor.subtitles.paddle_unavailable_reason",
        lambda: None,
    )
    monkeypatch.setattr(
        "corrcut.ui.editor.subtitles.check_toolchain",
        lambda: _Toolchain(),
    )
    monkeypatch.setattr(
        "corrcut.ui.editor.subtitles.convert_bitmap_stream_to_cues",
        _block_on_cancel,
    )
    notices: list[str] = []

    def _notice(*args: object, **_kwargs: object) -> QMessageBox.StandardButton:
        notices.append(str(args))
        return QMessageBox.StandardButton.Ok

    monkeypatch.setattr("corrcut.ui.editor.subtitles.QMessageBox.information", _notice)

    assert editor_window._subtitle_extract_job_runner.run_callable(
        "Extract subtitles",
        lambda: _block_until_cancel(cancel_event=editor_window._subtitle_extract_job_runner.cancel_event),
    )
    editor_window._start_bitmap_subtitle_conversion()
    qtbot.waitUntil(lambda: editor_window._subtitle_job_runner.is_busy, timeout=3000)

    assert editor_window._subtitle_extract_job_runner.is_busy
    assert not any("Wait for" in note for note in notices)
    _assert_editor_stays_interactive(editor_window)

    editor_window._subtitle_job_runner.cancel_active()
    editor_window._subtitle_extract_job_runner.cancel_active()
    qtbot.waitUntil(
        lambda: not editor_window._subtitle_job_runner.is_busy
        and not editor_window._subtitle_extract_job_runner.is_busy,
        timeout=3000,
    )


def test_bitmap_ocr_pauses_scene_cuts_then_resumes(
    editor_window: EditorWindow,
    qtbot,  # type: ignore[no-untyped-def]
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    media = tmp_path / "clip.mkv"
    media.write_bytes(b"x")
    editor_window._project = _ocr_project(media)
    editor_window._duration = 12.0
    editor_window._waveform_gate_open_for_scene_cuts = True
    detect_calls: list[int] = []

    def _detect(*_args: object, **kwargs: object) -> list[float]:
        detect_calls.append(1)
        _block_until_cancel(**kwargs)
        return []

    monkeypatch.setattr("corrcut.ui.editor.scene_cuts.detect_scene_cuts", _detect)
    monkeypatch.setattr(
        "corrcut.ui.editor.subtitles.paddle_unavailable_reason",
        lambda: None,
    )
    monkeypatch.setattr(
        "corrcut.ui.editor.subtitles.check_toolchain",
        lambda: _Toolchain(),
    )
    monkeypatch.setattr(
        "corrcut.ui.editor.subtitles.convert_bitmap_stream_to_cues",
        _block_on_cancel,
    )

    editor_window._start_scene_cut_job()
    qtbot.waitUntil(lambda: len(detect_calls) == 1, timeout=3000)

    editor_window._start_bitmap_subtitle_conversion()
    qtbot.waitUntil(lambda: editor_window._subtitle_job_runner.is_busy, timeout=3000)
    assert editor_window._scene_cut_job_runner.cancel_event.is_set()
    qtbot.waitUntil(lambda: not editor_window._scene_cut_job_runner.is_busy, timeout=3000)
    assert len(detect_calls) == 1

    editor_window._subtitle_job_runner.cancel_active()
    qtbot.waitUntil(lambda: len(detect_calls) == 2, timeout=3000)
    editor_window._scene_cut_job_runner.cancel_active()
    qtbot.waitUntil(lambda: not editor_window._scene_cut_job_runner.is_busy, timeout=3000)
