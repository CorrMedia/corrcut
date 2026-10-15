"""Relink helpers and editor integration."""

from __future__ import annotations

from pathlib import Path

import pytest

from corrcut.domain.dialogue_target import DialogueTargetStrategy
from corrcut.domain.project import Project, SourceFileRecord, TrackRecord
from corrcut.domain.relink import (
    apply_relink,
    fingerprint_matches,
    missing_track_stream_indices,
)
from corrcut.engine.probe import AudioStreamInfo, MediaProbeResult
from corrcut.ui.editor_window import EditorWindow


def _audio_stream(index: int) -> AudioStreamInfo:
    return AudioStreamInfo(
        index=index,
        codec="aac",
        channel_layout="stereo",
        channels=2,
        sample_rate=48000,
        language="eng",
        title=None,
        is_default=index == 1,
        dialogue_target_strategy=DialogueTargetStrategy.FULL_MIX,
    )


def test_fingerprint_matches_requires_all_fields() -> None:
    base = {"size": 100, "head_sha256": "a", "tail_sha256": "b"}
    assert fingerprint_matches(base, dict(base))
    assert not fingerprint_matches(base, {**base, "size": 101})


def test_missing_track_stream_indices() -> None:
    project = Project(
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
            TrackRecord(
                stream_index=3,
                codec="aac",
                channel_layout="stereo",
                channels=2,
                sample_rate=48000,
                language="eng",
                dialogue_target_strategy=DialogueTargetStrategy.FULL_MIX,
            ),
        ],
    )
    probe = MediaProbeResult(
        path=Path("movie.mkv"),
        format_name="matroska",
        duration_seconds=10.0,
        video_streams=[],
        audio_streams=[_audio_stream(1)],
        subtitle_streams=[],
    )
    assert missing_track_stream_indices(project, probe) == [3]


def _surround_71_stream(index: int, *, is_default: bool = False) -> AudioStreamInfo:
    return AudioStreamInfo(
        index=index,
        codec="truehd",
        channel_layout="7.1(wide-side)",
        channels=8,
        sample_rate=48000,
        language="eng",
        title="TrueHD 7.1",
        is_default=is_default,
        dialogue_target_strategy=DialogueTargetStrategy.CENTER_CHANNEL,
    )


def _surround_stream(index: int) -> AudioStreamInfo:
    return AudioStreamInfo(
        index=index,
        codec="ac3",
        channel_layout="5.1(side)",
        channels=6,
        sample_rate=48000,
        language="eng",
        title="Main",
        is_default=True,
        dialogue_target_strategy=DialogueTargetStrategy.CENTER_CHANNEL,
    )


def test_apply_relink_rebuilds_tracks_from_probe(tmp_path: Path) -> None:
    old = tmp_path / "old.mkv"
    new = tmp_path / "cropped.mkv"
    new.write_bytes(b"x" * 8)
    probe = MediaProbeResult(
        path=new,
        format_name="matroska",
        duration_seconds=12.5,
        video_streams=[],
        audio_streams=[_audio_stream(1)],
        subtitle_streams=[],
    )
    project = Project(
        source=SourceFileRecord(path=old, fingerprint={"size": 1}, probe={"duration_seconds": 1.0}),
        tracks=[
            TrackRecord(
                stream_index=1,
                codec="ac3",
                channel_layout="5.1(side)",
                channels=6,
                sample_rate=48000,
                language="eng",
                dialogue_target_strategy=DialogueTargetStrategy.CENTER_CHANNEL,
            ),
            TrackRecord(
                stream_index=2,
                codec="ac3",
                channel_layout="5.1(side)",
                channels=6,
                sample_rate=48000,
                language="spa",
                dialogue_target_strategy=DialogueTargetStrategy.CENTER_CHANNEL,
            ),
        ],
        selected_audio_stream_index=2,
    )
    apply_relink(project, new, probe, {"size": 8, "head_sha256": "abc", "tail_sha256": ""})
    assert len(project.tracks) == 1
    track = project.tracks[0]
    assert track.stream_index == 1
    assert track.channel_layout == "stereo"
    assert track.channels == 2
    assert track.dialogue_target_strategy == DialogueTargetStrategy.FULL_MIX
    assert project.selected_audio_stream_index == 1


def test_refresh_probe_keeps_selected_stream_when_still_present() -> None:
    project = Project(
        source=SourceFileRecord(
            path=Path("movie.mkv"),
            fingerprint={"size": 1},
            probe={"duration_seconds": 10.0},
        ),
        tracks=[
            TrackRecord(
                stream_index=1,
                codec="ac3",
                channel_layout="5.1(side)",
                channels=6,
                sample_rate=48000,
                language="eng",
                dialogue_target_strategy=DialogueTargetStrategy.CENTER_CHANNEL,
            )
        ],
        selected_audio_stream_index=1,
    )
    probe = MediaProbeResult(
        path=Path("movie.mkv"),
        format_name="matroska",
        duration_seconds=10.0,
        video_streams=[],
        audio_streams=[_surround_stream(1), _audio_stream(2)],
        subtitle_streams=[],
    )
    project.refresh_probe_metadata(probe)
    assert [t.stream_index for t in project.tracks] == [1, 2]
    assert project.selected_audio_stream_index == 1
    assert project.tracks[0].dialogue_target_strategy == DialogueTargetStrategy.CENTER_CHANNEL
    assert project.tracks[1].dialogue_target_strategy == DialogueTargetStrategy.FULL_MIX


def test_default_audio_prefers_7_1_over_container_stereo() -> None:
    probe = MediaProbeResult(
        path=Path("movie.mkv"),
        format_name="matroska",
        duration_seconds=10.0,
        video_streams=[],
        audio_streams=[_audio_stream(1), _surround_71_stream(2)],
        subtitle_streams=[],
    )
    project = Project.from_probe(probe, {"size": 1, "head_sha256": "a", "tail_sha256": ""})
    assert project.selected_audio_stream_index == 2
    selected = project.selected_track()
    assert selected is not None
    assert selected.channel_layout == "7.1(wide-side)"
    assert selected.dialogue_target_strategy == DialogueTargetStrategy.CENTER_CHANNEL


def test_from_probe_prefers_unlabeled_8ch_over_default_stereo() -> None:
    atmos = AudioStreamInfo(
        index=2,
        codec="truehd",
        channel_layout=None,
        channels=16,
        sample_rate=48000,
        language="eng",
        title="TrueHD Atmos",
        is_default=False,
        dialogue_target_strategy=DialogueTargetStrategy.FULL_MIX,
    )
    probe = MediaProbeResult(
        path=Path("movie.mkv"),
        format_name="matroska",
        duration_seconds=10.0,
        video_streams=[],
        audio_streams=[_audio_stream(1), atmos],
        subtitle_streams=[],
    )
    project = Project.from_probe(probe, {"size": 1, "head_sha256": "a", "tail_sha256": ""})
    assert project.selected_audio_stream_index == 2


def test_refresh_keeps_stereo_selection_when_7_1_appears() -> None:
    project = Project(
        source=SourceFileRecord(
            path=Path("movie.mkv"),
            fingerprint={"size": 1},
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
    )
    probe = MediaProbeResult(
        path=Path("movie.mkv"),
        format_name="matroska",
        duration_seconds=10.0,
        video_streams=[],
        audio_streams=[_audio_stream(1), _surround_71_stream(2)],
        subtitle_streams=[],
    )
    project.refresh_probe_metadata(probe)
    assert project.selected_audio_stream_index == 1


def test_from_probe_stereo_only_selects_full_mix() -> None:
    probe = MediaProbeResult(
        path=Path("stereo.mkv"),
        format_name="matroska",
        duration_seconds=10.0,
        video_streams=[],
        audio_streams=[_audio_stream(1)],
        subtitle_streams=[],
    )
    project = Project.from_probe(probe, {"size": 1, "head_sha256": "a", "tail_sha256": ""})
    assert project.selected_audio_stream_index == 1
    selected = project.selected_track()
    assert selected is not None
    assert selected.channel_layout == "stereo"
    assert selected.dialogue_target_strategy == DialogueTargetStrategy.FULL_MIX
    from corrcut.engine.waveform import _audio_filter_for_track, _channel_key_for_track

    assert _channel_key_for_track(selected) == "stereo"
    assert _audio_filter_for_track(selected, "stereo") == "pan=mono|c0=0.5*FL+0.5*FR"


def test_prefers_titled_71_downmix_when_all_tracks_are_stereo() -> None:
    labeled = AudioStreamInfo(
        index=2,
        codec="aac",
        channel_layout="stereo",
        channels=2,
        sample_rate=48000,
        language="eng",
        title="Surround 7.1",
        is_default=False,
        dialogue_target_strategy=DialogueTargetStrategy.FULL_MIX,
    )
    probe = MediaProbeResult(
        path=Path("movie.mkv"),
        format_name="matroska",
        duration_seconds=10.0,
        video_streams=[],
        audio_streams=[_audio_stream(1), labeled],
        subtitle_streams=[],
    )
    project = Project.from_probe(probe, {"size": 1, "head_sha256": "a", "tail_sha256": ""})
    assert project.selected_audio_stream_index == 2
    selected = project.selected_track()
    assert selected is not None
    assert selected.title == "Surround 7.1"
    assert selected.channel_layout == "stereo"


def test_relink_keeps_stream_when_it_became_stereo(
    tmp_path: Path,
) -> None:
    old = tmp_path / "old.mkv"
    new = tmp_path / "cropped.mkv"
    new.write_bytes(b"x" * 8)
    project = Project(
        source=SourceFileRecord(path=old, fingerprint={"size": 1}, probe={"duration_seconds": 1.0}),
        tracks=[
            TrackRecord(
                stream_index=1,
                codec="ac3",
                channel_layout="5.1(side)",
                channels=6,
                sample_rate=48000,
                language="eng",
                dialogue_target_strategy=DialogueTargetStrategy.CENTER_CHANNEL,
            )
        ],
        selected_audio_stream_index=1,
    )
    probe = MediaProbeResult(
        path=new,
        format_name="matroska",
        duration_seconds=12.5,
        video_streams=[],
        audio_streams=[_audio_stream(1), _surround_71_stream(2)],
        subtitle_streams=[],
    )
    apply_relink(project, new, probe, {"size": 8, "head_sha256": "abc", "tail_sha256": ""})
    assert project.selected_audio_stream_index == 1
    selected = project.selected_track()
    assert selected is not None
    assert selected.channels == 2


def test_apply_relink_updates_source(tmp_path: Path) -> None:
    old = tmp_path / "old.mkv"
    new = tmp_path / "new.mkv"
    new.write_bytes(b"x" * 8)
    probe = MediaProbeResult(
        path=new,
        format_name="matroska",
        duration_seconds=12.5,
        video_streams=[],
        audio_streams=[_audio_stream(1)],
        subtitle_streams=[],
    )
    fp = {"size": 8, "head_sha256": "abc", "tail_sha256": ""}
    project = Project(
        source=SourceFileRecord(path=old, fingerprint={"size": 1}, probe={"duration_seconds": 1.0}),
        tracks=[
            TrackRecord(
                stream_index=1,
                codec="aac",
                channel_layout="stereo",
                channels=2,
                sample_rate=48000,
                language=None,
                dialogue_target_strategy=DialogueTargetStrategy.FULL_MIX,
            ),
        ],
        selected_audio_stream_index=1,
    )
    apply_relink(project, new, probe, fp)
    assert project.source is not None
    assert project.source.path == new.resolve()
    assert project.source.fingerprint == fp
    assert project.source.probe["duration_seconds"] == pytest.approx(12.5)


def _project_with_source(path: Path) -> Project:
    return Project(
        source=SourceFileRecord(
            path=path,
            fingerprint={"size": 1},
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


def test_relink_action_enabled_when_media_missing(editor_window: EditorWindow) -> None:
    missing = Path(__file__).with_name("definitely_missing_media.mkv")
    editor_window._set_project(_project_with_source(missing))
    assert editor_window._relink_action.isEnabled()
    assert editor_window._preview_panel._media_path is None


def test_relink_action_enabled_when_media_present(
    editor_window: EditorWindow,
    tmp_path: Path,
) -> None:
    media = tmp_path / "movie.mkv"
    media.write_bytes(b"x" * 8)
    editor_window._set_project(_project_with_source(media))
    assert editor_window._relink_action.isEnabled()
