"""Merge Whisper and subtitle detections into one suggestion per utterance."""

from __future__ import annotations

from corrcut.ai.detect import merge_overlapping_suggestions
from corrcut.ai.match_events import same_detection_event
from corrcut.domain.ai_suggestion import AiSuggestion, DetectionSource


def _longer_phrase(a: str, b: str) -> str:
    return a if len(a) >= len(b) else b


def _combine_suggestions(primary: AiSuggestion, secondary: AiSuggestion) -> None:
    """Merge *secondary* into *primary* (mutates primary). Prefer primary timing (Whisper)."""
    primary.confidence = min(1.0, max(primary.confidence, secondary.confidence) * 1.05)
    primary.whisper_probability = max(primary.whisper_probability, secondary.whisper_probability)
    primary.transcript_phrase = _longer_phrase(
        primary.transcript_phrase, secondary.transcript_phrase
    )
    if primary.alignment_quality == "good" or secondary.alignment_quality == "good":
        primary.alignment_quality = "good"
    primary.detection_source = DetectionSource.BOTH.value
    if not primary.category and secondary.category:
        primary.category = secondary.category


def merge_cross_source(
    whisper_suggestions: list[AiSuggestion],
    subtitle_suggestions: list[AiSuggestion],
    *,
    slop_ms: int,
) -> list[AiSuggestion]:
    """
    Keep Whisper hits; fold subtitle hits into duplicates or append subs-only gaps.
    """
    merged: list[AiSuggestion] = list(whisper_suggestions)
    for sub_hit in subtitle_suggestions:
        duplicate = next(
            (w for w in merged if same_detection_event(w, sub_hit, slop_ms=slop_ms)),
            None,
        )
        if duplicate is not None:
            _combine_suggestions(duplicate, sub_hit)
            continue
        merged.append(sub_hit)
    return merge_overlapping_suggestions(merged)
