"""Cached embedded subtitle cues and the reader that uses them."""

from __future__ import annotations

from pathlib import Path

import pytest

from corrcut.domain.project import Project, SourceFileRecord
from corrcut.domain.subtitle_cue import SubtitleCue, SubtitleSource
from corrcut.domain.time_point import TimePoint
from corrcut.engine.subtitles.ingest import (
    embedded_text_stream_label,
    read_embedded_subtitle_cues,
)
from corrcut.io.subtitle_cache import (
    load_embedded_subtitle_cache,
    save_embedded_subtitle_cache,
)

_FINGERPRINT = {"size": 4, "head_sha256": "abc", "tail_sha256": ""}


def _cue() -> SubtitleCue:
    return SubtitleCue.create(
        start=TimePoint.from_seconds_float(1.0, 48_000),
        end=TimePoint.from_seconds_float(2.0, 48_000),
        text="Hello",
        source=SubtitleSource.EMBEDDED,
    )


def _project(path: Path) -> Project:
    return Project(
        source=SourceFileRecord(
            path=path,
            fingerprint=dict(_FINGERPRINT),
            probe={
                "duration_seconds": 10.0,
                "subtitle_streams": [
                    {"index": 3, "codec": "subrip", "language": "eng", "title": None}
                ],
            },
        )
    )


def test_subtitle_cache_round_trip(tmp_path: Path) -> None:
    original = _cue()
    saved = save_embedded_subtitle_cache(tmp_path, _FINGERPRINT, 3, [original])
    assert saved is not None and saved.is_file()

    loaded = load_embedded_subtitle_cache(tmp_path, _FINGERPRINT, 3)
    assert loaded is not None
    assert len(loaded) == 1
    assert loaded[0].text == "Hello"
    assert loaded[0].start == original.start
    assert loaded[0].end == original.end
    assert loaded[0].id != original.id
    assert load_embedded_subtitle_cache(tmp_path, _FINGERPRINT, 4) is None


def test_read_embedded_uses_cache_and_skips_ffmpeg(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    media = tmp_path / "Title.mkv"
    media.write_bytes(b"x")
    cache = tmp_path / "cache"
    save_embedded_subtitle_cache(cache, _FINGERPRINT, 3, [_cue()])

    def _fail(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("ffmpeg extract should not run on a cache hit")

    monkeypatch.setattr(
        "corrcut.engine.subtitles.ingest.extract_subtitle_srt",
        _fail,
    )

    found = read_embedded_subtitle_cues(_project(media), cache_dir=cache)
    assert found is not None
    stream_index, cues = found
    assert stream_index == 3
    assert cues[0].text == "Hello"


def test_read_embedded_writes_cache(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    media = tmp_path / "Title.mkv"
    media.write_bytes(b"x")
    cache = tmp_path / "cache"
    calls: list[int] = []

    def _extract(_source: Path, index: int, output: Path, **_kwargs: object) -> None:
        calls.append(index)
        output.write_text(
            "1\n00:00:01,000 --> 00:00:02,000\nHello there\n",
            encoding="utf-8",
        )

    monkeypatch.setattr(
        "corrcut.engine.subtitles.ingest.extract_subtitle_srt",
        _extract,
    )

    project = _project(media)
    first = read_embedded_subtitle_cues(project, cache_dir=cache)
    second = read_embedded_subtitle_cues(project, cache_dir=cache)

    assert calls == [3]
    assert first is not None and second is not None
    assert first[1][0].text == "Hello there"
    assert second[1][0].text == "Hello there"


def test_embedded_text_stream_label_uses_probe(tmp_path: Path) -> None:
    media = tmp_path / "Title.mkv"
    media.write_bytes(b"x")
    assert embedded_text_stream_label(_project(media)) == "#3 subrip (eng)"
