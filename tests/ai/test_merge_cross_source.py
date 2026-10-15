"""Cross-source suggestion merge (Whisper + subtitles)."""

from fractions import Fraction

from corrcut.ai.merge_cross_source import merge_cross_source
from corrcut.domain.ai_suggestion import AiSuggestion, DetectionSource
from corrcut.domain.operation import TimeRange
from corrcut.domain.time_point import TimePoint


def _suggestion(
    term: str,
    start: float,
    end: float,
    *,
    source: str = DetectionSource.WHISPER.value,
    confidence: float = 0.9,
) -> AiSuggestion:
    return AiSuggestion.create(
        stream_index=1,
        time_range=TimeRange(
            TimePoint(Fraction(start)),
            TimePoint(Fraction(end)),
        ),
        matched_word=term,
        matched_term=term,
        transcript_phrase=f"phrase {term}",
        confidence=confidence,
        whisper_probability=confidence,
        alignment_quality="good",
        detection_source=source,
    )


def test_merge_combines_offset_duplicate() -> None:
    whisper = [_suggestion("fuck", 12.4, 12.9, source=DetectionSource.WHISPER.value)]
    subs = [
        _suggestion(
            "fuck",
            12.0,
            13.2,
            source=DetectionSource.SUBTITLE.value,
            confidence=0.85,
        )
    ]
    merged = merge_cross_source(whisper, subs, slop_ms=750)
    assert len(merged) == 1
    assert merged[0].detection_source == DetectionSource.BOTH.value
    assert merged[0].time_range.start.seconds == Fraction(12.4)
    assert merged[0].confidence >= 0.9


def test_merge_keeps_subs_only_gap() -> None:
    whisper = [_suggestion("shit", 1.0, 2.0)]
    subs = [_suggestion("damn", 50.0, 51.0, source=DetectionSource.SUBTITLE.value)]
    merged = merge_cross_source(whisper, subs, slop_ms=750)
    assert len(merged) == 2
    terms = {s.matched_term for s in merged}
    assert terms == {"shit", "damn"}


def test_merge_different_terms_same_time() -> None:
    whisper = [_suggestion("shit", 10.0, 11.0)]
    subs = [_suggestion("fuck", 10.2, 11.2, source=DetectionSource.SUBTITLE.value)]
    merged = merge_cross_source(whisper, subs, slop_ms=750)
    assert len(merged) == 2
