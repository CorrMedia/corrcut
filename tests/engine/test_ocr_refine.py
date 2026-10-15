"""Whisper-guided OCR refinement tests."""

from __future__ import annotations

from fractions import Fraction

from corrcut.ai.transcript import TranscriptWord
from corrcut.domain.project import Project
from corrcut.domain.subtitle_cue import SubtitleCue, SubtitleSource
from corrcut.domain.time_point import TimePoint
from corrcut.domain.whisper_cache import WhisperCache
from corrcut.engine.subtitles.cue_sets import install_cue_set_for_tests
from corrcut.engine.subtitles.ocr_refine import refine_ocr_text, refine_project_ocr_cues


def _word(
    text: str,
    start: float,
    end: float,
    *,
    probability: float = 0.92,
    segment_text: str | None = None,
) -> TranscriptWord:
    return TranscriptWord(
        text=text,
        start=TimePoint.from_seconds_float(start, 48_000),
        end=TimePoint.from_seconds_float(end, 48_000),
        probability=probability,
        segment_text=segment_text or text,
    )


def test_refine_ocr_text_fixes_lyric_misreads_with_whisper() -> None:
    whisper_words = [
        _word("'Cause", 1.0, 1.2),
        _word("morning", 1.2, 1.6),
        _word("rolls", 1.6, 2.0),
        _word("around", 2.0, 2.5),
    ]

    refined = refine_ocr_text(
        "'Cause moming rolis around",
        whisper_words,
        start_seconds=1.0,
        end_seconds=2.5,
    )

    assert refined == "'Cause morning rolls around"


def test_refine_ocr_text_keeps_structural_dialogue_markers() -> None:
    whisper_words = [
        _word("Hello", 1.0, 1.4),
        _word("there", 1.4, 1.8),
    ]

    refined = refine_ocr_text(
        "« Hello there",
        whisper_words,
        start_seconds=1.0,
        end_seconds=1.8,
    )

    assert refined == "- Hello there"


def test_refine_ocr_text_without_whisper_uses_structural_cleanup_only() -> None:
    refined = refine_ocr_text(
        "« Hello",
        [],
        start_seconds=1.0,
        end_seconds=2.0,
    )
    assert refined == "- Hello"


def test_refine_ocr_text_finds_late_speech_after_early_on_screen_cue() -> None:
    whisper_words = [
        _word("Yeah", 8.5, 8.8),
        _word("Cause", 11.0, 11.2),
        _word("morning", 11.2, 11.6),
        _word("rolls", 11.6, 12.0),
        _word("around", 12.0, 12.4),
        _word("buddy", 12.5, 12.8),
    ]

    refined = refine_ocr_text(
        "Cause moming rolis around",
        whisper_words,
        start_seconds=8.0,
        end_seconds=9.5,
    )

    assert refined == "Cause morning rolls around"


def test_refine_ocr_text_ignores_neighbor_words_in_search_window() -> None:
    whisper_words = [
        _word("Well", 4.8, 5.0),
        _word("Hello", 5.0, 5.3),
        _word("there", 5.3, 5.6),
        _word("friend", 5.7, 6.0),
    ]

    refined = refine_ocr_text(
        "Hello thare",
        whisper_words,
        start_seconds=5.0,
        end_seconds=5.6,
    )

    assert refined == "Hello there"


def test_refine_ocr_text_stitches_fragmented_whisper_cues() -> None:
    whisper_words = [
        _word("I", 10.0, 10.1, segment_text="I need"),
        _word("need", 10.1, 10.3, segment_text="I need"),
        _word("to", 11.0, 11.1, segment_text="to leave now"),
        _word("leave", 11.1, 11.4, segment_text="to leave now"),
        _word("now", 11.4, 11.7, segment_text="to leave now"),
    ]

    refined = refine_ocr_text(
        "I need to leeve now",
        whisper_words,
        start_seconds=8.0,
        end_seconds=9.0,
    )

    assert refined == "I need to leave now"


def test_refine_ocr_text_ignores_filler_words_between_whisper_cues() -> None:
    whisper_words = [
        _word("um", 4.9, 5.0, segment_text="um"),
        _word("Hello", 5.0, 5.3, segment_text="Hello there"),
        _word("there", 5.3, 5.6, segment_text="Hello there"),
        _word("friend", 5.7, 6.0, segment_text="friend"),
    ]

    refined = refine_ocr_text(
        "Hello thare",
        whisper_words,
        start_seconds=5.0,
        end_seconds=5.6,
    )

    assert refined == "Hello there"


def test_refine_ocr_text_fixes_greyhound_line_with_late_speech() -> None:
    whisper_words = [
        _word("If", 100.0, 100.2),
        _word("I", 100.2, 100.3),
        _word("hit", 100.3, 100.5),
        _word("a", 100.5, 100.6),
        _word("Greyhound", 100.6, 101.0),
        _word("station", 101.0, 101.4),
        _word("west", 101.4, 101.7),
        _word("of", 101.7, 101.8),
        _word("Santa", 101.8, 102.1),
        _word("Fe", 102.1, 102.4),
    ]

    refined = refine_ocr_text(
        "1 if him et a Greyhound station weet of Santa Fe",
        whisper_words,
        start_seconds=98.0,
        end_seconds=99.5,
    )

    assert refined == "If I hit a Greyhound station west of Santa Fe"


def test_refine_project_ocr_cues_uses_whisper_cache() -> None:
    cue = SubtitleCue.create(
        start=TimePoint(Fraction(1)),
        end=TimePoint(Fraction(3)),
        text="'Cause moming rolis around",
        source=SubtitleSource.OCR,
    )
    project = Project(selected_audio_stream_index=1)
    project.whisper_cache = WhisperCache.from_transcript_words(
        stream_index=1,
        model="base",
        words=[
            _word("'Cause", 1.0, 1.2),
            _word("morning", 1.2, 1.6),
            _word("rolls", 1.6, 2.0),
            _word("around", 2.0, 2.8),
        ],
    )
    install_cue_set_for_tests(project, [cue], source=SubtitleSource.OCR)

    changed = refine_project_ocr_cues(project)

    assert changed == 1
    assert project.subtitle_cues[0].text == "'Cause morning rolls around"
    assert project.subtitle_cues[0].edited_text == "'Cause morning rolls around"
