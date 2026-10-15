"""Waveform follows the selected audio track, including same-layout dubs."""

from __future__ import annotations

from corrcut.config import AppPaths
from corrcut.domain.dialogue_target import DialogueTargetStrategy
from corrcut.domain.project import Project, SourceFileRecord, TrackRecord
from corrcut.engine.waveform import PeakStore
from corrcut.ui.editor_window import EditorWindow


def _track(stream_index: int, *, language: str, layout: str, channels: int) -> TrackRecord:
    strategy = (
        DialogueTargetStrategy.CENTER_CHANNEL
        if channels > 2
        else DialogueTargetStrategy.FULL_MIX
    )
    return TrackRecord(
        stream_index=stream_index,
        codec="aac",
        channel_layout=layout,
        channels=channels,
        sample_rate=48000,
        language=language,
        dialogue_target_strategy=strategy,
    )


def test_same_layout_track_switch_builds_waveform_after_busy_job(
    editor_window: EditorWindow,
    tmp_path,
    monkeypatch,
) -> None:
    media = tmp_path / "clip.mkv"
    media.write_bytes(b"x")
    editor_window._paths = AppPaths(
        app_data=tmp_path,
        cache_dir=tmp_path / "cache",
        log_dir=tmp_path / "logs",
        temp_dir=tmp_path / "temp",
    )
    built: list[int] = []

    def _run_callable(label: str, fn: object) -> bool:
        del label, fn
        track = editor_window._project.selected_track() if editor_window._project else None
        built.append(-1 if track is None else track.stream_index)
        return True

    monkeypatch.setattr(editor_window._waveform_job_runner, "run_callable", _run_callable)
    monkeypatch.setattr(PeakStore, "open_or_none", classmethod(lambda cls, *args, **kwargs: None))

    editor_window._set_project(
        Project(
            source=SourceFileRecord(path=media, fingerprint={"head_sha256": "abc"}, probe={}),
            tracks=[
                _track(1, language="eng", layout="7.1", channels=8),
                _track(2, language="spa", layout="stereo", channels=2),
                _track(3, language="fra", layout="stereo", channels=2),
            ],
            selected_audio_stream_index=1,
        )
    )

    runner = editor_window._waveform_job_runner
    runner._busy = True
    editor_window._on_track_selected(2)
    editor_window._on_track_selected(3)
    assert built == []
    assert editor_window._project is not None
    assert editor_window._project.selected_audio_stream_index == 3

    runner._busy = False
    runner.busy_changed.emit(False)
    assert built == [3]
