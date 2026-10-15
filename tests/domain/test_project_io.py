from fractions import Fraction
from pathlib import Path

import pytest

from corrcut.domain.dialogue_target import DialogueTargetStrategy
from corrcut.domain.operation import Operation, OperationType
from corrcut.domain.project import Project, SourceFileRecord, TrackRecord
from corrcut.domain.scene_cuts import SceneCutCache
from corrcut.domain.time_point import TimePoint
from corrcut.domain.timeline_flag import TimelineFlag
from corrcut.domain.video_matte import VideoMatteCache
from corrcut.io.project_store import load_project, save_project


def test_save_load_round_trip(tmp_path: Path) -> None:
    project = Project(
        source=SourceFileRecord(
            path=Path("C:/media/test.mkv"),
            fingerprint={"size": 100, "head_sha256": "abc"},
            probe={"duration_seconds": 120.0, "audio_streams": []},
        ),
        tracks=[
            TrackRecord(
                stream_index=1,
                codec="aac",
                channel_layout="5.1",
                channels=6,
                sample_rate=48000,
                language="eng",
                dialogue_target_strategy=DialogueTargetStrategy.CENTER_CHANNEL,
            )
        ],
        operations=[
            Operation.create(
                OperationType.VOLUME,
                1,
                ["FC"],
                TimePoint(Fraction(10, 1)),
                TimePoint(Fraction(11, 1)),
            )
        ],
        selected_audio_stream_index=1,
    )
    path = tmp_path / "test.corrcut"
    save_project(project, path)
    loaded = load_project(path)
    assert loaded.project_version == project.project_version
    assert len(loaded.operations) == 1
    assert loaded.operations[0].type == OperationType.VOLUME
    assert loaded.operations[0].time_range.start.seconds == Fraction(10, 1)


def test_save_load_preserves_timeline_flags(tmp_path: Path) -> None:
    project = Project(selected_audio_stream_index=1)
    flag = TimelineFlag.create(4.5, 2)
    flag.color = "#112233"
    project.timeline_flags.append(flag)
    path = tmp_path / "flags.corrcut"
    save_project(project, path)
    loaded = load_project(path)
    assert len(loaded.timeline_flags) == 1
    assert loaded.timeline_flags[0].slot == 2
    assert loaded.timeline_flags[0].seconds == 4.5
    assert loaded.timeline_flags[0].color == "#112233"


def test_missing_timeline_flags_load_empty() -> None:
    project = Project.from_json(
        {
            "project_version": 2,
            "source_files": [],
            "tracks": [],
            "operations": [],
        }
    )
    assert project.timeline_flags == []


def test_save_load_preserves_scene_cuts(tmp_path: Path) -> None:
    fp = {"size": 100, "head_sha256": "abc", "tail_sha256": "def"}
    project = Project(
        source=SourceFileRecord(
            path=Path("C:/media/test.mkv"),
            fingerprint=fp,
            probe={"duration_seconds": 120.0, "audio_streams": []},
        ),
        scene_cuts=SceneCutCache(threshold=0.4, fingerprint=fp, times=[12.345, 40.12]),
        selected_audio_stream_index=1,
    )
    path = tmp_path / "cuts.corrcut"
    save_project(project, path)
    loaded = load_project(path)
    assert loaded.scene_cuts is not None
    assert loaded.scene_cuts.threshold == 0.4
    assert loaded.scene_cuts.fingerprint == fp
    assert loaded.scene_cuts.times == pytest.approx([12.345, 40.12])


def test_missing_scene_cuts_load_empty() -> None:
    project = Project.from_json(
        {
            "project_version": 2,
            "source_files": [],
            "tracks": [],
            "operations": [],
        }
    )
    assert project.scene_cuts is None


def test_save_load_preserves_letterbox_zoom_settings(tmp_path: Path) -> None:
    fp = {"size": 100, "head_sha256": "abc", "tail_sha256": "def"}
    project = Project(
        source=SourceFileRecord(
            path=Path("C:/media/test.mkv"),
            fingerprint=fp,
            probe={"duration_seconds": 120.0, "audio_streams": []},
        ),
        preserve_letterbox_on_zoom=False,
        video_matte=VideoMatteCache(
            x=0.0,
            y=0.125,
            width=1.0,
            height=0.75,
            limit=24.0,
            fingerprint=fp,
            detector="gray_scan_v2",
        ),
        selected_audio_stream_index=1,
    )
    path = tmp_path / "matte.corrcut"
    save_project(project, path)
    loaded = load_project(path)
    assert loaded.preserve_letterbox_on_zoom is False
    assert loaded.video_matte is not None
    assert loaded.video_matte.y == pytest.approx(0.125)
    assert loaded.video_matte.height == pytest.approx(0.75)
    assert loaded.video_matte.fingerprint == fp


def test_missing_letterbox_settings_default_on() -> None:
    project = Project.from_json(
        {
            "project_version": 2,
            "source_files": [],
            "tracks": [],
            "operations": [],
        }
    )
    assert project.preserve_letterbox_on_zoom is True
    assert project.video_matte is None


def test_scene_cut_cache_matches_fingerprint() -> None:
    fp = {"size": 1, "head_sha256": "a", "tail_sha256": "b"}
    cache = SceneCutCache(threshold=0.4, fingerprint=fp, times=[1.0])
    assert cache.matches(fp, threshold=0.4)
    assert not cache.matches({**fp, "size": 2}, threshold=0.4)
    assert not cache.matches(fp, threshold=0.3)


def test_save_project_keeps_previous_file_when_replace_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "movie.corrcut"
    original = '{"project_version": 2, "keep": true}'
    path.write_text(original, encoding="utf-8")

    def _fail_replace(*_args: object, **_kwargs: object) -> None:
        raise OSError("disk full")

    monkeypatch.setattr("corrcut.io.atomic_write.os.replace", _fail_replace)
    with pytest.raises(OSError, match="disk full"):
        save_project(Project(), path)

    assert path.read_text(encoding="utf-8") == original
    assert list(tmp_path.glob("*.tmp")) == []
