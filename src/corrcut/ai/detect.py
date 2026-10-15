"""Build censor-window suggestions from transcript words."""

from __future__ import annotations

from fractions import Fraction
from typing import Literal

from corrcut.ai.normalize import normalize_token
from corrcut.ai.phrase_match import (
    PhrasePattern,
    is_blocked_by_exceptions,
    phrase_tokens_match,
)
from corrcut.ai.swear_lexicon import SwearLexicon
from corrcut.ai.transcript import TranscriptWord
from corrcut.domain.ai_suggestion import (
    AiSuggestion,
    DetectionSettings,
    DetectionSource,
)
from corrcut.domain.operation import TimeRange
from corrcut.domain.time_point import TimePoint

TimingSource = Literal["audio", "subtitle"]


def _padding_seconds(
    settings: DetectionSettings,
    *,
    low_alignment: bool,
    timing_source: TimingSource,
) -> tuple[Fraction, Fraction]:
    pre_ms = settings.pre_padding_ms
    post_ms = settings.post_padding_ms
    if timing_source == "subtitle":
        pre_ms += settings.subtitle_extra_padding_ms
        post_ms += settings.subtitle_extra_padding_ms
    if low_alignment:
        extra = settings.low_confidence_extra_padding_ms
        pre_ms += extra
        post_ms += extra
    return Fraction(pre_ms, 1000), Fraction(post_ms, 1000)


def _phrase_context(words: list[TranscriptWord], start: int, end: int, *, radius: int = 2) -> str:
    lo = max(0, start - radius)
    hi = min(len(words), end + radius + 1)
    return " ".join(w.text for w in words[lo:hi]).strip()


def _word_norms(words: list[TranscriptWord]) -> list[str]:
    return [normalize_token(w.text) for w in words]


def _trigger_index_in_phrase(pattern: PhrasePattern) -> int:
    key_norm = normalize_token(pattern.canonical)
    try:
        return pattern.tokens.index(key_norm)
    except ValueError:
        return 0


def _find_phrase_hits(
    words: list[TranscriptWord],
    word_norms: list[str],
    patterns: tuple[PhrasePattern, ...],
) -> tuple[list[tuple[int, int, PhrasePattern, float]], set[int]]:
    """Return phrase hits and word indices consumed by phrase matching."""
    consumed: set[int] = set()
    hits: list[tuple[int, int, PhrasePattern, float]] = []
    count = len(words)

    for pattern in patterns:
        width = len(pattern.tokens)
        if width < 2 or count < width:
            continue
        for start in range(count - width + 1):
            if any(index in consumed for index in range(start, start + width)):
                continue
            window = word_norms[start : start + width]
            if not phrase_tokens_match(window, pattern.tokens):
                continue
            center = start + width // 2
            if is_blocked_by_exceptions(word_norms, center, pattern.exceptions):
                continue

            if pattern.mute_span == "phrase":
                mute_start = start
                mute_end = start + width - 1
            else:
                rel = _trigger_index_in_phrase(pattern)
                mute_start = mute_end = start + rel

            probs = [words[i].probability for i in range(mute_start, mute_end + 1)]
            min_prob = min(probs) if probs else 1.0
            hits.append((mute_start, mute_end, pattern, min_prob))
            for index in range(start, start + width):
                consumed.add(index)

    return hits, consumed


def detect_suggestions(
    words: list[TranscriptWord],
    *,
    stream_index: int,
    settings: DetectionSettings,
    lexicon: SwearLexicon | None = None,
    track_language: str | None = None,
    duration_seconds: float | None = None,
    timing_source: TimingSource = "audio",
    detection_source: str | None = None,
) -> list[AiSuggestion]:
    """Match phrase patterns then word lexicon; emit padded censor windows."""
    if not words:
        return []

    swear_lexicon = lexicon or SwearLexicon.build(track_language=track_language)
    word_lexicon = swear_lexicon.words
    source_label = detection_source or (
        DetectionSource.SUBTITLE.value
        if timing_source == "subtitle"
        else DetectionSource.WHISPER.value
    )

    max_end = TimePoint(Fraction(duration_seconds)) if duration_seconds else None
    suggestions: list[AiSuggestion] = []
    word_norms = _word_norms(words)

    phrase_hits, consumed = _find_phrase_hits(words, word_norms, swear_lexicon.phrases)

    for mute_start, mute_end, pattern, min_prob in phrase_hits:
        suggestions.append(
            _suggestion_from_span(
                words,
                stream_index=stream_index,
                settings=settings,
                mute_start=mute_start,
                mute_end=mute_end,
                matched_term=pattern.canonical,
                category=pattern.category,
                strength=1.0,
                whisper_probability=min_prob,
                max_end=max_end,
                matched_display=pattern.raw,
                timing_source=timing_source,
                detection_source=source_label,
            )
        )

    for index, word in enumerate(words):
        if index in consumed:
            continue
        match = word_lexicon.match(word.text, word_norms=word_norms, word_index=index)
        if match is None:
            continue
        suggestions.append(
            _suggestion_from_span(
                words,
                stream_index=stream_index,
                settings=settings,
                mute_start=index,
                mute_end=index,
                matched_term=match.term,
                category=match.category,
                strength=match.strength,
                whisper_probability=word.probability,
                max_end=max_end,
                matched_display=word.text,
                timing_source=timing_source,
                detection_source=source_label,
            )
        )

    return merge_overlapping_suggestions(suggestions)


def _suggestion_from_span(
    words: list[TranscriptWord],
    *,
    stream_index: int,
    settings: DetectionSettings,
    mute_start: int,
    mute_end: int,
    matched_term: str,
    category: str,
    strength: float,
    whisper_probability: float,
    max_end: TimePoint | None,
    matched_display: str,
    timing_source: TimingSource,
    detection_source: str,
) -> AiSuggestion:
    if timing_source == "subtitle":
        low_alignment = True
        text_prob = max(whisper_probability, 0.92)
    else:
        low_alignment = whisper_probability < settings.whisper_word_confidence_cutoff
        text_prob = whisper_probability
    pre_pad, post_pad = _padding_seconds(
        settings, low_alignment=low_alignment, timing_source=timing_source
    )
    start = TimePoint(words[mute_start].start.seconds - pre_pad)
    end = TimePoint(words[mute_end].end.seconds + post_pad)
    if start.seconds < 0:
        start = TimePoint(Fraction(0))
    if max_end is not None and end.seconds > max_end.seconds:
        end = max_end

    confidence = min(1.0, max(0.0, text_prob * strength))
    return AiSuggestion.create(
        stream_index=stream_index,
        time_range=TimeRange(start=start, end=end),
        matched_word=matched_display,
        matched_term=matched_term,
        transcript_phrase=_phrase_context(words, mute_start, mute_end),
        confidence=confidence,
        whisper_probability=whisper_probability,
        alignment_quality="low" if low_alignment else "good",
        category=category,
        detection_source=detection_source,
    )


def merge_overlapping_suggestions(suggestions: list[AiSuggestion]) -> list[AiSuggestion]:
    if len(suggestions) < 2:
        return suggestions
    ordered = sorted(
        suggestions,
        key=lambda s: (s.time_range.start.seconds, -s.confidence),
    )
    merged: list[AiSuggestion] = []
    for suggestion in ordered:
        if not merged:
            merged.append(suggestion)
            continue
        prev = merged[-1]
        if (
            prev.stream_index == suggestion.stream_index
            and prev.matched_term.casefold() == suggestion.matched_term.casefold()
            and suggestion.time_range.start.seconds <= prev.time_range.end.seconds
        ):
            if suggestion.confidence > prev.confidence:
                merged[-1] = suggestion
            continue
        merged.append(suggestion)
    return merged
