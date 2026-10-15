"""Shared rules for whether two detections refer to the same utterance."""

from __future__ import annotations

from fractions import Fraction

from corrcut.domain.ai_suggestion import AiSuggestion
from corrcut.domain.operation import TimeRange

DEFAULT_CROSS_SOURCE_MERGE_SLOP_MS = 750


def time_ranges_overlap(a: TimeRange, b: TimeRange) -> bool:
    return a.start.seconds <= b.end.seconds and b.start.seconds <= a.end.seconds


def suggestion_center_seconds(suggestion: AiSuggestion) -> Fraction:
    return (suggestion.time_range.start.seconds + suggestion.time_range.end.seconds) / 2


def centers_within_slop(
    a: AiSuggestion,
    b: AiSuggestion,
    *,
    slop_ms: int,
) -> bool:
    slop = Fraction(slop_ms, 1000)
    return abs(suggestion_center_seconds(a) - suggestion_center_seconds(b)) <= slop


def same_detection_event(
    a: AiSuggestion,
    b: AiSuggestion,
    *,
    slop_ms: int = DEFAULT_CROSS_SOURCE_MERGE_SLOP_MS,
) -> bool:
    """True when two suggestions are the same profanity hit (possibly different sources)."""
    if a.stream_index != b.stream_index:
        return False
    if a.matched_term.casefold() != b.matched_term.casefold():
        return False
    return time_ranges_overlap(a.time_range, b.time_range) or centers_within_slop(
        a, b, slop_ms=slop_ms
    )
