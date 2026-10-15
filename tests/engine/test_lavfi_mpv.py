from fractions import Fraction
from pathlib import Path

from corrcut.domain.dialogue_target import DialogueTargetStrategy
from corrcut.domain.operation import Operation, OperationType
from corrcut.domain.project import Project, SourceFileRecord, TrackRecord
from corrcut.domain.time_point import TimePoint
from corrcut.engine.filtergraph import CompileContext, FiltergraphCompiler
from corrcut.preview.lavfi_mpv import (
    MPV_AUDIO_IN,
    MPV_AUDIO_OUT,
    MPV_PASSTHROUGH,
    MPV_VIDEO_IN,
    MPV_VIDEO_OUT,
    MPV_VIDEO_PASSTHROUGH,
    compile_playback_lavfi,
    compile_preview_filtergraph,
    ffmpeg_graph_to_mpv_lavfi,
)
from corrcut.preview.playback_profile import PlaybackProfile


def _project_with_mute() -> Project:
    track = TrackRecord(
        stream_index=1,
        codec="aac",
        channel_layout="stereo",
        channels=2,
        sample_rate=48000,
        language="eng",
        dialogue_target_strategy=DialogueTargetStrategy.FULL_MIX,
    )
    op = Operation.create(
        OperationType.MUTE,
        1,
        [],
        TimePoint(Fraction(1)),
        TimePoint(Fraction(2)),
    )
    return Project(
        source=SourceFileRecord(path=Path(__file__), fingerprint={}, probe={"duration_seconds": 10.0}),
        tracks=[track],
        selected_audio_stream_index=1,
        operations=[op],
    )


def test_ffmpeg_graph_to_mpv_relabels_streams() -> None:
    graph = "[0:a:0]volume=volume='1'[aout]"
    out = ffmpeg_graph_to_mpv_lavfi(graph)
    assert MPV_AUDIO_IN in out
    assert MPV_AUDIO_OUT in out
    assert "0:a" not in out
    assert "[aout]" not in out


def test_playback_lavfi_matches_preview_compiler_with_mpv_labels() -> None:
    project = _project_with_mute()
    compiled = compile_preview_filtergraph(project)
    assert compiled is not None
    preview = compile_playback_lavfi(project, edits_enabled=True)
    assert preview is not None
    audio = ffmpeg_graph_to_mpv_lavfi(
        compiled.filter_complex,
        output_label=compiled.output_audio_label,
    )
    # Audio-only projects still need a video identity pad so [vo] stays wired
    # after a prior video effect graph (otherwise the picture goes blank).
    assert preview == f"{MPV_VIDEO_PASSTHROUGH};{audio}"


def test_playback_lavfi_keeps_vo_after_video_ops_removed() -> None:
    from corrcut.domain.operation import ITEM_LEVEL_STREAM_INDEX

    track = TrackRecord(
        stream_index=1,
        codec="aac",
        channel_layout="stereo",
        channels=2,
        sample_rate=48000,
        language="eng",
        dialogue_target_strategy=DialogueTargetStrategy.FULL_MIX,
    )
    blur = Operation.create(
        OperationType.BLUR,
        ITEM_LEVEL_STREAM_INDEX,
        [],
        TimePoint(Fraction(1)),
        TimePoint(Fraction(2)),
    )
    mute = Operation.create(
        OperationType.MUTE,
        1,
        [],
        TimePoint(Fraction(1)),
        TimePoint(Fraction(2)),
    )
    with_blur = Project(
        source=SourceFileRecord(
            path=Path(__file__), fingerprint={}, probe={"duration_seconds": 10.0}
        ),
        tracks=[track],
        selected_audio_stream_index=1,
        operations=[blur, mute],
    )
    after_delete = Project(
        source=with_blur.source,
        tracks=with_blur.tracks,
        selected_audio_stream_index=1,
        operations=[mute],
    )
    before = compile_playback_lavfi(with_blur, edits_enabled=True)
    after = compile_playback_lavfi(after_delete, edits_enabled=True)
    assert before is not None and "scale=" in before
    assert after is not None
    assert MPV_VIDEO_IN in after and MPV_VIDEO_OUT in after
    assert "null" in after  # video passthrough
    assert "scale=" not in after


def test_playback_lavfi_passthrough_when_ab_original() -> None:
    project = _project_with_mute()
    lavfi = compile_playback_lavfi(project, edits_enabled=False)
    assert lavfi == MPV_PASSTHROUGH
    assert lavfi.startswith(MPV_VIDEO_IN)
    assert MPV_VIDEO_OUT in lavfi
    assert MPV_AUDIO_IN in lavfi
    assert MPV_AUDIO_OUT in lavfi


def test_playback_uses_second_audio_stream_ordinal() -> None:
    project = _project_with_mute()
    project.tracks.append(
        TrackRecord(
            stream_index=2,
            codec="aac",
            channel_layout="stereo",
            channels=2,
            sample_rate=48000,
            language="spa",
            dialogue_target_strategy=DialogueTargetStrategy.FULL_MIX,
        )
    )
    project.selected_audio_stream_index = 2
    track = project.selected_track()
    assert track is not None
    export = FiltergraphCompiler().compile_for_audio_stream(
        project.operations_for_selected_track(),
        CompileContext(track=track, filter_time_origin_s=0.0),
        audio_ordinal=project.audio_ordinal_for_stream(2),
    )
    assert "[0:a:1]" in export.filter_complex
    preview = compile_playback_lavfi(project)
    assert preview is not None
    assert "[0:a:1]" not in preview
    assert MPV_AUDIO_IN in preview


def _project_center_channel_fc_mute() -> Project:
    track = TrackRecord(
        stream_index=1,
        codec="ac3",
        channel_layout="5.1",
        channels=6,
        sample_rate=48000,
        language="eng",
        dialogue_target_strategy=DialogueTargetStrategy.CENTER_CHANNEL,
    )
    op = Operation.create(
        OperationType.MUTE,
        1,
        ["FC"],
        TimePoint(Fraction(1)),
        TimePoint(Fraction(2)),
    )
    return Project(
        source=SourceFileRecord(path=Path(__file__), fingerprint={}, probe={"duration_seconds": 10.0}),
        tracks=[track],
        selected_audio_stream_index=1,
        operations=[op],
    )


def test_playback_center_channel_graph_uses_mpv_placeholders() -> None:
    """5.1 center-channel path must emit [aid]/[ao], not raw FFmpeg stream labels."""
    preview = compile_playback_lavfi(_project_center_channel_fc_mute(), edits_enabled=True)
    assert preview is not None
    assert preview.startswith(MPV_VIDEO_PASSTHROUGH)
    assert MPV_AUDIO_IN in preview
    assert "asplit=6" in preview
    assert MPV_AUDIO_OUT in preview
    assert "0:a" not in preview


def test_playback_center_channel_fc_mute_targets_fc_pad_only() -> None:
    """FC-scoped mute applies volume on fc_src/fc_e, not on the full mix input."""
    preview = compile_playback_lavfi(_project_center_channel_fc_mute(), edits_enabled=True)
    assert preview is not None
    assert "pan=mono|c0=FC[fc_src]" in preview or "[fc_src]" in preview
    assert "join=inputs=6" in preview
    # Full-mix path would be [aid]volume=... without per-channel split.
    assert preview.index("volume=") > preview.index("[fc")


def test_passthrough_constant_matches_compile_when_edits_disabled() -> None:
    project = _project_center_channel_fc_mute()
    assert compile_playback_lavfi(project, edits_enabled=False) == MPV_PASSTHROUGH


def test_playback_profile_can_drop_mute_from_the_graph() -> None:
    project = _project_with_mute()
    full = compile_playback_lavfi(project, edits_enabled=True)
    narrowed = compile_playback_lavfi(
        project,
        edits_enabled=True,
        profile=PlaybackProfile.full().without(actions={"mute"}),
    )
    assert full is not None and full != MPV_PASSTHROUGH
    assert narrowed == MPV_PASSTHROUGH
