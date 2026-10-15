"""Project file round-trip for subtitle cues."""

from __future__ import annotations

from fractions import Fraction
from pathlib import Path

from corrcut.domain.subtitle_cue import SubtitleCue, SubtitleSource
from corrcut.domain.subtitle_settings import SubtitleSettings
from corrcut.domain.time_point import TimePoint
from corrcut.domain.whisper_cache import WhisperCache
from corrcut.domain.project import Project, SourceFileRecord
from corrcut.engine.subtitles.cue_sets import install_cue_set_for_tests
from corrcut.io.project_store import load_project, save_project


def test_save_load_subtitle_cues_round_trip(tmp_path: Path) -> None:
    cue = SubtitleCue.create(
        start=TimePoint(Fraction(1)),
        end=TimePoint(Fraction(2)),
        text="What the fuck",
        edited_text="What the ***",
        source=SubtitleSource.EMBEDDED,
        user_edited=True,
        cue_id="cue-1",
    )
    settings = SubtitleSettings(
        stream_index=3,
    )
    project = Project(
        source=SourceFileRecord(
            path=Path("C:/media/test.mkv"),
            fingerprint={"size": 100},
            probe={"duration_seconds": 120.0, "subtitle_streams": []},
        ),
        subtitle_settings=settings,
        whisper_cache=WhisperCache.from_transcript_words(
            stream_index=1,
            model="base",
            words=[],
        ),
    )
    install_cue_set_for_tests(project, [cue], source=SubtitleSource.EMBEDDED)
    path = tmp_path / "subs.corrcut"
    save_project(project, path)
    loaded = load_project(path)
    assert len(loaded.subtitle_cues) == 1
    restored = loaded.subtitle_cues[0]
    assert restored.id == "cue-1"
    assert restored.text == "What the fuck"
    assert restored.edited_text == "What the ***"
    assert restored.user_edited is True
    assert restored.source == SubtitleSource.EMBEDDED
    assert loaded.subtitle_settings.stream_index == 3
    assert loaded.whisper_cache is not None
    assert loaded.whisper_cache.stream_index == 1
    assert loaded.active_subtitle_source == SubtitleSource.EMBEDDED
    assert "embedded" in loaded.subtitle_cue_sets
