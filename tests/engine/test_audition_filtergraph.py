"""FFmpeg accepts compiled audition graphs for multichannel layouts."""

from __future__ import annotations

import shutil
from fractions import Fraction
from pathlib import Path

import pytest

from corrcut.domain.dialogue_target import DialogueTargetStrategy
from corrcut.domain.operation import Operation, OperationType
from corrcut.domain.project import Project, SourceFileRecord, TrackRecord
from corrcut.domain.time_point import TimePoint
from corrcut.engine.audition import AuditionSpec, audition_job_spec


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not on PATH")
def test_audition_spec_71_with_mute_uses_front_bed_preview_mix(tmp_path: Path) -> None:
    track = TrackRecord(
        stream_index=0,
        codec="aac",
        channel_layout="7.1",
        channels=8,
        sample_rate=48000,
        language=None,
        dialogue_target_strategy=DialogueTargetStrategy.CENTER_CHANNEL,
    )
    op = Operation.create(
        OperationType.MUTE,
        0,
        ["FC"],
        TimePoint(Fraction(1)),
        TimePoint(Fraction(2)),
    )
    project = Project(
        source=SourceFileRecord(path=tmp_path / "dummy.mkv", fingerprint={}, probe={}),
        tracks=[track],
        operations=[op],
        selected_audio_stream_index=0,
    )
    out = tmp_path / "audition.wav"
    spec = AuditionSpec(output_wav=out, start_seconds=Fraction(0))
    job = audition_job_spec(project, spec)
    assert job is not None
    fc = job.command[job.command.index("-filter_complex") + 1]
    assert "asplit=3" in fc
    assert "pan=mono|c0=FL" in fc
    assert "pan=mono|c0=FR" in fc
    assert "pan=mono|c0=FC" in fc
    assert "amix=inputs=3" in fc
    assert "volume=0.0:enable='between(t," in fc


def test_audition_no_ops_center_channel_uses_fc_pan() -> None:
    track = TrackRecord(
        stream_index=0,
        codec="aac",
        channel_layout="5.1(side)",
        channels=6,
        sample_rate=48000,
        language=None,
        dialogue_target_strategy=DialogueTargetStrategy.CENTER_CHANNEL,
    )
    project = Project(
        source=SourceFileRecord(path=Path("dummy.mkv"), fingerprint={}, probe={}),
        tracks=[track],
        operations=[],
        selected_audio_stream_index=0,
    )
    job = audition_job_spec(
        project,
        AuditionSpec(output_wav=Path("out.wav"), start_seconds=Fraction(2892, 1)),
    )
    assert job is not None
    fc = job.command[job.command.index("-filter_complex") + 1]
    assert "pan=mono|c0=FC" in fc
    assert "anull" not in fc
    assert job.command.index("-ss") < job.command.index("-i")


def test_audition_job_uses_input_seek_before_input() -> None:
    track = TrackRecord(
        stream_index=0,
        codec="aac",
        channel_layout="5.1(side)",
        channels=6,
        sample_rate=48000,
        language=None,
        dialogue_target_strategy=DialogueTargetStrategy.CENTER_CHANNEL,
    )
    op = Operation.create(
        OperationType.MUTE,
        0,
        ["FC"],
        TimePoint(Fraction(289285, 100)),
        TimePoint(Fraction(28934, 10)),
    )
    project = Project(
        source=SourceFileRecord(path=Path("dummy.mkv"), fingerprint={}, probe={}),
        tracks=[track],
        operations=[op],
        selected_audio_stream_index=0,
    )
    job = audition_job_spec(
        project,
        AuditionSpec(output_wav=Path("out.wav"), start_seconds=Fraction(2892875, 1000)),
    )
    assert job is not None
    assert job.command.index("-ss") < job.command.index("-i")


def test_center_audition_preview_includes_front_bed_while_muting_scoped_channel() -> None:
    track = TrackRecord(
        stream_index=0,
        codec="aac",
        channel_layout="5.1(side)",
        channels=6,
        sample_rate=48000,
        language=None,
        dialogue_target_strategy=DialogueTargetStrategy.CENTER_CHANNEL,
    )
    op = Operation.create(
        OperationType.MUTE,
        0,
        ["FC"],
        TimePoint(Fraction(1)),
        TimePoint(Fraction(2)),
    )
    project = Project(
        source=SourceFileRecord(path=Path("dummy.mkv"), fingerprint={}, probe={}),
        tracks=[track],
        operations=[op],
        selected_audio_stream_index=0,
    )
    job = audition_job_spec(
        project,
        AuditionSpec(output_wav=Path("out.wav"), start_seconds=Fraction(3, 2)),
    )
    assert job is not None
    fc = job.command[job.command.index("-filter_complex") + 1]
    assert "asplit=3" in fc
    assert "pan=mono|c0=FL" in fc
    assert "pan=mono|c0=FR" in fc
    assert "pan=mono|c0=FC" in fc
    assert "amix=inputs=3" in fc
    assert fc.count("volume=0.0:enable='between(t,") == 1


def test_audition_inside_mute_keeps_playhead_offset_inside_clip_window() -> None:
    track = TrackRecord(
        stream_index=0,
        codec="aac",
        channel_layout="5.1(side)",
        channels=6,
        sample_rate=48000,
        language=None,
        dialogue_target_strategy=DialogueTargetStrategy.CENTER_CHANNEL,
    )
    op = Operation.create(
        OperationType.MUTE,
        0,
        ["FC"],
        TimePoint(Fraction(289285, 100)),
        TimePoint(Fraction(28934, 10)),
    )
    project = Project(
        source=SourceFileRecord(path=Path("dummy.mkv"), fingerprint={}, probe={}),
        tracks=[track],
        operations=[op],
        selected_audio_stream_index=0,
    )
    # Playhead 0.025s after mute starts; with 0.5s pre-roll, clip-relative offset is 0.5s.
    job = audition_job_spec(
        project,
        AuditionSpec(output_wav=Path("out.wav"), start_seconds=Fraction(2892875, 1000)),
    )
    assert job is not None
    fc = job.command[job.command.index("-filter_complex") + 1]
    from corrcut.engine.audition import audition_seek_seconds

    seek_s = audition_seek_seconds(float(Fraction(2892875, 1000)))
    clip_playhead = float(Fraction(2892875, 1000)) - seek_s
    rel_mute_start = float(op.time_range.start.seconds) - seek_s
    rel_mute_end = float(op.time_range.end.seconds) - seek_s
    assert rel_mute_start <= clip_playhead <= rel_mute_end
    assert "volume=0.0:enable='between(t," in fc
