"""Subtitle cue set selection."""

from __future__ import annotations

from fractions import Fraction

from corrcut.domain.project import Project
from corrcut.domain.subtitle_cue import SubtitleCue, SubtitleSource
from corrcut.domain.time_point import TimePoint
from corrcut.domain.whisper_cache import WhisperCache
from corrcut.ai.transcript import TranscriptWord
from corrcut.engine.subtitles.cue_sets import (
    best_available_source,
    coerce_subtitle_source,
    install_cue_set_for_tests,
    promote_source_if_better,
    set_cues_for_source,
    switch_active_source,
)
from corrcut.engine.subtitles.ingest import ingest_whisper_subtitles


def _cue(text: str, source: SubtitleSource) -> SubtitleCue:
    return SubtitleCue.create(
        start=TimePoint(Fraction(1)),
        end=TimePoint(Fraction(2)),
        text=text,
        source=source,
    )


def test_coerce_subtitle_source_accepts_enum_and_string() -> None:
    assert coerce_subtitle_source(SubtitleSource.WHISPER) == SubtitleSource.WHISPER
    assert coerce_subtitle_source("whisper") == SubtitleSource.WHISPER
    assert coerce_subtitle_source("invalid") is None


def test_best_available_source_prefers_embedded_over_whisper() -> None:
    project = Project()
    install_cue_set_for_tests(
        project, [_cue("wh", SubtitleSource.WHISPER)], source=SubtitleSource.WHISPER
    )
    set_cues_for_source(project, SubtitleSource.EMBEDDED, [_cue("em", SubtitleSource.EMBEDDED)])
    assert best_available_source(project) == SubtitleSource.EMBEDDED


def test_promote_source_if_better_switches_to_ocr_from_whisper() -> None:
    project = Project()
    install_cue_set_for_tests(
        project, [_cue("wh", SubtitleSource.WHISPER)], source=SubtitleSource.WHISPER
    )
    set_cues_for_source(project, SubtitleSource.OCR, [_cue("ocr", SubtitleSource.OCR)])
    promote_source_if_better(project, SubtitleSource.OCR)
    assert project.active_subtitle_source == SubtitleSource.OCR


def test_switch_active_source_changes_active_cues() -> None:
    project = Project()
    install_cue_set_for_tests(
        project, [_cue("wh", SubtitleSource.WHISPER)], source=SubtitleSource.WHISPER
    )
    set_cues_for_source(project, SubtitleSource.OCR, [_cue("ocr", SubtitleSource.OCR)])
    project.active_subtitle_source = SubtitleSource.WHISPER
    assert switch_active_source(project, SubtitleSource.OCR)
    assert project.subtitle_cues[0].text == "ocr"


def test_ingest_whisper_builds_cue_set_alongside_ocr() -> None:
    project = Project(selected_audio_stream_index=1)
    set_cues_for_source(project, SubtitleSource.OCR, [_cue("ocr line", SubtitleSource.OCR)])
    project.active_subtitle_source = SubtitleSource.OCR
    project.whisper_cache = WhisperCache.from_transcript_words(
        stream_index=1,
        model="base",
        words=[
            TranscriptWord(
                text="Hello",
                start=TimePoint(Fraction(1)),
                end=TimePoint(Fraction(2)),
                probability=0.9,
                segment_text="Hello",
            )
        ],
    )

    assert ingest_whisper_subtitles(project)

    sources = project.available_subtitle_sources()
    assert SubtitleSource.OCR in sources
    assert SubtitleSource.WHISPER in sources
    assert project.active_subtitle_source == SubtitleSource.OCR
