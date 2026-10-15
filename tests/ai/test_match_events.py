"""Detection event matching for dedupe."""

from fractions import Fraction

from corrcut.ai.match_events import same_detection_event
from corrcut.domain.ai_suggestion import AiSuggestion
from corrcut.domain.operation import TimeRange
from corrcut.domain.time_point import TimePoint


def _hit(term: str, start: float, end: float) -> AiSuggestion:
    return AiSuggestion.create(
        stream_index=1,
        time_range=TimeRange(TimePoint(Fraction(start)), TimePoint(Fraction(end))),
        matched_word=term,
        matched_term=term,
        transcript_phrase=term,
        confidence=0.9,
        whisper_probability=0.9,
        alignment_quality="good",
    )


def test_same_event_by_center_slop_without_overlap() -> None:
    a = _hit("fuck", 10.0, 10.5)
    b = _hit("fuck", 10.55, 11.05)
    assert not same_detection_event(a, b, slop_ms=200)
    assert same_detection_event(a, b, slop_ms=750)


def test_different_terms_not_same_event() -> None:
    a = _hit("shit", 10.0, 11.0)
    b = _hit("fuck", 10.0, 11.0)
    assert not same_detection_event(a, b, slop_ms=750)
