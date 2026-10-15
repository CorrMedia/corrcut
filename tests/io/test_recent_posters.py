"""Cached recent-project poster stills."""

from __future__ import annotations

from pathlib import Path

import pytest

from corrcut.domain.project import Project, SourceFileRecord
from corrcut.io.recent_posters import (
    POSTER_HEIGHT,
    POSTER_WIDTH,
    capture_recent_poster,
    discard_recent_poster,
    poster_cache_path,
    schedule_recent_poster,
)


def _project(tmp_path: Path, *, media: Path | None, duration: float | None = 90.0) -> Project:
    source = None
    if media is not None:
        probe: dict[str, object] = {}
        if duration is not None:
            probe["duration_seconds"] = duration
        source = SourceFileRecord(path=media, fingerprint={}, probe=probe)
    project = Project(source=source)
    project.project_path = tmp_path / "Night.corrcut"
    return project


def test_poster_path_is_stable_for_a_project(tmp_path: Path) -> None:
    project = tmp_path / "Night.corrcut"
    first = poster_cache_path(tmp_path / "cache", project)
    second = poster_cache_path(tmp_path / "cache", project)
    assert first == second
    assert first.parent.name == "recent_posters"
    assert first.suffix == ".jpg"


def test_capture_writes_a_still_ten_percent_in(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    media = tmp_path / "night.mkv"
    media.touch()
    dest = tmp_path / "posters" / "night.jpg"
    seen: dict[str, object] = {}

    def extract(
        media_path: Path,
        dest_path: Path,
        time_s: float,
        *,
        width: int,
        height: int,
        duration: float,
        ffmpeg: str | None = None,
        cancel_event: object = None,
    ) -> None:
        seen["media"] = media_path
        seen["time"] = time_s
        seen["width"] = width
        seen["height"] = height
        seen["duration"] = duration
        dest_path.parent.mkdir(parents=True, exist_ok=True)
        dest_path.write_bytes(b"jpeg")

    monkeypatch.setattr("corrcut.io.recent_posters.extract_frame", extract)
    assert capture_recent_poster(media, dest, 90.0)
    assert dest.read_bytes() == b"jpeg"
    assert seen == {
        "media": media,
        "time": 9.0,
        "width": POSTER_WIDTH,
        "height": POSTER_HEIGHT,
        "duration": 90.0,
    }


def test_capture_skips_when_duration_is_unknown(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    called = False

    def extract(*_args: object, **_kwargs: object) -> None:
        nonlocal called
        called = True

    monkeypatch.setattr("corrcut.io.recent_posters.extract_frame", extract)
    dest = tmp_path / "posters" / "night.jpg"
    assert capture_recent_poster(tmp_path / "night.mkv", dest, None) is False
    assert called is False
    assert not dest.exists()


def test_schedule_skips_when_the_poster_already_exists(tmp_path: Path) -> None:
    media = tmp_path / "night.mkv"
    media.touch()
    project = _project(tmp_path, media=media)
    assert project.project_path is not None
    dest = poster_cache_path(tmp_path / "cache", project.project_path)
    dest.parent.mkdir(parents=True)
    dest.write_bytes(b"jpeg")
    started: list[object] = []
    schedule_recent_poster(tmp_path / "cache", project, start=started.append)
    assert started == []


def test_schedule_captures_when_a_poster_is_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    media = tmp_path / "night.mkv"
    media.touch()
    project = _project(tmp_path, media=media, duration=1.5)
    captured: list[tuple[Path, float | None]] = []

    def capture(media_path: Path, dest: Path, duration: float | None) -> bool:
        captured.append((media_path, duration))
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(b"jpeg")
        return True

    monkeypatch.setattr("corrcut.io.recent_posters.capture_recent_poster", capture)
    schedule_recent_poster(tmp_path / "cache", project, start=lambda run: run())
    assert captured == [(media, 1.5)]


def test_schedule_ignores_a_project_without_media(tmp_path: Path) -> None:
    project = _project(tmp_path, media=None)
    started: list[object] = []
    schedule_recent_poster(tmp_path / "cache", project, start=started.append)
    assert started == []


def test_discard_removes_the_cached_still(tmp_path: Path) -> None:
    project = tmp_path / "Night.corrcut"
    dest = poster_cache_path(tmp_path / "cache", project)
    dest.parent.mkdir(parents=True)
    dest.write_bytes(b"jpeg")
    discard_recent_poster(tmp_path / "cache", project)
    assert not dest.exists()
    discard_recent_poster(tmp_path / "cache", project)
