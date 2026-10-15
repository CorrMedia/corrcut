"""Refine OCR subtitle text using structural cleanup and Whisper alignment."""

from __future__ import annotations

import re
from collections.abc import Callable
from difflib import SequenceMatcher
from threading import Event
from typing import TYPE_CHECKING

from corrcut.ai.transcript import TranscriptWord
from corrcut.engine.subtitles.ocr_cleanup import (
    _DIALOGUE_DASH,
    _MUSIC_NOTE,
    _MUSIC_NOTES,
    clean_ocr_text,
)
from corrcut.engine.subtitles.words_to_cues import DEFAULT_MAX_GAP_SECONDS

if TYPE_CHECKING:
    from corrcut.domain.project import Project

_MIN_WHISPER_WORD_PROBABILITY = 0.35
_FULL_REPLACE_SIMILARITY = 0.95
_TOKEN_KEEP_OCR_SIMILARITY = 0.93
_MIN_SPAN_SCORE = 0.42
_MIN_TEXT_MATCH_FOR_FUSION = 0.48
_MIN_SEARCH_PAD_BACK_SECONDS = 2.5
_MIN_SEARCH_PAD_FORWARD_SECONDS = 8.0
_TEXT_MATCH_WEIGHT = 0.65
_TIME_OVERLAP_WEIGHT = 0.1
_CENTER_ALIGN_WEIGHT = 0.1
_LENGTH_MATCH_WEIGHT = 0.1
_OCR_COVERAGE_WEIGHT = 0.05

_NORM_RE = re.compile(r"[^a-z0-9']+", re.IGNORECASE)
_APOSTROPHE_RE = re.compile(r"^['\u2018\u2019\u02bc\u0060]+")


def refine_ocr_text(
    text: str,
    whisper_words: list[TranscriptWord],
    *,
    start_seconds: float,
    end_seconds: float,
) -> str:
    """Apply structural cleanup, then align cue text with the best Whisper word span."""
    structural = clean_ocr_text(text)
    if not structural or not whisper_words:
        return structural

    if "\n" in structural:
        refined_lines: list[str] = []
        for line in structural.split("\n"):
            stripped = line.strip()
            if not stripped:
                continue
            refined_lines.append(
                _refine_single_line(
                    stripped,
                    whisper_words,
                    start_seconds=start_seconds,
                    end_seconds=end_seconds,
                )
            )
        return "\n".join(refined_lines)

    return _refine_single_line(
        structural,
        whisper_words,
        start_seconds=start_seconds,
        end_seconds=end_seconds,
    )


def refine_project_ocr_cues(
    project: Project,
    *,
    progress: Callable[[int, str], None] | None = None,
    cancel_event: Event | None = None,
) -> int:
    """Refine stored OCR cues. Returns number of cues whose text or display changed."""
    from corrcut.domain.subtitle_cue import SubtitleSource
    from corrcut.engine.subtitles.cue_sets import cues_for_source, set_cues_for_source

    cues = cues_for_source(project, SubtitleSource.OCR)
    if not cues:
        return 0

    whisper_words = _project_whisper_words(project)
    changed = 0
    total = len(cues)
    for index, cue in enumerate(cues):
        if cancel_event is not None and cancel_event.is_set():
            break
        if progress is not None and total:
            percent = int(100 * index / total)
            progress(percent, f"Cleaning OCR cue {index + 1} of {total}")

        if cue.user_edited:
            refined_text = refine_ocr_text(
                cue.text,
                whisper_words,
                start_seconds=float(cue.start.seconds),
                end_seconds=float(cue.end.seconds),
            )
            if refined_text and refined_text != cue.text:
                cue.text = refined_text
                changed += 1
            continue

        source = cue.edited_text or cue.text
        refined = refine_ocr_text(
            source,
            whisper_words,
            start_seconds=float(cue.start.seconds),
            end_seconds=float(cue.end.seconds),
        )
        if not refined:
            continue
        if refined != cue.text or refined != cue.edited_text:
            cue.text = refined
            cue.edited_text = refined
            changed += 1

    set_cues_for_source(project, SubtitleSource.OCR, cues)
    if progress is not None:
        progress(100, "OCR cleanup finished")
    return changed


def reapply_ocr_cleanup(
    project: Project,
    *,
    progress: Callable[[int, str], None] | None = None,
    cancel_event: Event | None = None,
) -> int:
    """Backward-compatible entry point for UI reapply."""
    return refine_project_ocr_cues(project, progress=progress, cancel_event=cancel_event)


def _refine_single_line(
    ocr_line: str,
    whisper_words: list[TranscriptWord],
    *,
    start_seconds: float,
    end_seconds: float,
) -> str:
    span = _find_best_whisper_span(
        ocr_line,
        whisper_words,
        start_seconds=start_seconds,
        end_seconds=end_seconds,
    )
    if not span or not _whisper_span_trusted(ocr_line, span):
        return ocr_line
    trimmed = _trim_filler_edges(ocr_line, span)
    return _fuse_line(ocr_line, trimmed)


def _whisper_span_trusted(ocr_line: str, span: list[TranscriptWord]) -> bool:
    ocr_token_count = max(1, len(ocr_line.split()))
    text_score = _token_similarity(ocr_line, span)
    coverage = _ocr_coverage_score(ocr_line, span)
    if text_score >= 0.62 and coverage >= 0.7:
        return True
    if ocr_token_count >= 8:
        min_coverage = 0.88
    elif ocr_token_count >= 4:
        min_coverage = 0.85
    else:
        min_coverage = 0.75
    return text_score >= _MIN_TEXT_MATCH_FOR_FUSION and coverage >= min_coverage


def _project_whisper_words(project: Project) -> list[TranscriptWord]:
    cache = project.whisper_cache
    if cache is None or not cache.words:
        return []
    return cache.transcript_words()


def _search_window_bounds(start_seconds: float, end_seconds: float) -> tuple[float, float]:
    duration = max(0.1, end_seconds - start_seconds)
    backward = max(_MIN_SEARCH_PAD_BACK_SECONDS, duration * 0.5)
    # Bitmap subtitles often appear on screen before the matching speech is heard.
    forward = max(_MIN_SEARCH_PAD_FORWARD_SECONDS, duration * 1.5)
    return start_seconds - backward, end_seconds + forward


def _words_in_search_window(
    words: list[TranscriptWord],
    start_seconds: float,
    end_seconds: float,
) -> list[TranscriptWord]:
    window_start, window_end = _search_window_bounds(start_seconds, end_seconds)
    selected = [
        word
        for word in words
        if float(word.end.seconds) >= window_start and float(word.start.seconds) <= window_end
    ]
    selected.sort(key=lambda word: float(word.start.seconds))
    return [word for word in selected if word.probability >= _MIN_WHISPER_WORD_PROBABILITY]


def _find_best_whisper_span(
    ocr_line: str,
    whisper_words: list[TranscriptWord],
    *,
    start_seconds: float,
    end_seconds: float,
) -> list[TranscriptWord]:
    candidates = _words_in_search_window(whisper_words, start_seconds, end_seconds)
    if not candidates:
        return []

    ocr_norm = _normalize(ocr_line)
    if not ocr_norm:
        return []

    ocr_token_count = max(1, len(ocr_line.split()))
    best_words: list[TranscriptWord] = []
    best_score = -1.0

    for span in _candidate_spans(candidates, ocr_token_count):
        score = _score_span(
            ocr_line,
            span,
            cue_start=start_seconds,
            cue_end=end_seconds,
            ocr_token_count=ocr_token_count,
        )
        if score > best_score:
            best_score = score
            best_words = span

    if best_score < _MIN_SPAN_SCORE:
        return []
    return best_words


def _candidate_spans(
    candidates: list[TranscriptWord],
    ocr_token_count: int,
) -> list[list[TranscriptWord]]:
    spans: list[list[TranscriptWord]] = []
    seen: set[tuple[int, ...]] = set()
    max_span = _max_span_length(ocr_token_count, len(candidates))

    def add_span(span: list[TranscriptWord]) -> None:
        if not span or len(span) > max_span:
            return
        key = tuple(id(word) for word in span)
        if key in seen:
            return
        seen.add(key)
        spans.append(span)

    for start_index in range(len(candidates)):
        upper = min(len(candidates), start_index + max_span)
        for end_index in range(start_index + 1, upper + 1):
            add_span(candidates[start_index:end_index])

    phrase_groups = _phrase_groups(candidates)
    max_phrase_groups = min(len(phrase_groups), max(4, (ocr_token_count + 1) // 2))
    for start_group in range(len(phrase_groups)):
        merged: list[TranscriptWord] = []
        for group_index in range(
            start_group, min(len(phrase_groups), start_group + max_phrase_groups)
        ):
            merged.extend(phrase_groups[group_index])
            add_span(list(merged))

    return spans


def _phrase_groups(words: list[TranscriptWord]) -> list[list[TranscriptWord]]:
    """Group words the same way Whisper cue generation does."""
    if not words:
        return []

    max_gap_seconds = float(DEFAULT_MAX_GAP_SECONDS)
    groups: list[list[TranscriptWord]] = []
    current: list[TranscriptWord] = []

    for word in words:
        if not current:
            current.append(word)
            continue
        prev = current[-1]
        gap = float(word.start.seconds) - float(prev.end.seconds)
        same_segment = word.segment_text == prev.segment_text
        if gap <= max_gap_seconds and same_segment:
            current.append(word)
        else:
            groups.append(current)
            current = [word]
    if current:
        groups.append(current)
    return groups


def _max_span_length(ocr_token_count: int, candidate_count: int) -> int:
    # Whisper often emits more word tokens than a single OCR/subtitle line shows.
    cap = max(ocr_token_count + 8, int(ocr_token_count * 2.5) + 2)
    return min(candidate_count, cap)


def _score_span(
    ocr_line: str,
    span: list[TranscriptWord],
    *,
    cue_start: float,
    cue_end: float,
    ocr_token_count: int,
) -> float:
    span_text = _join_whisper_words(span)
    if not _normalize(span_text):
        return -1.0

    text_score = _token_similarity(ocr_line, span)
    time_score = _aligned_temporal_overlap_score(cue_start, cue_end, ocr_line, span)
    center_score = _center_alignment_score(cue_start, cue_end, span)
    length_score = _length_match_score(ocr_line, span, ocr_token_count=ocr_token_count)
    coverage_score = _ocr_coverage_score(ocr_line, span)
    return (
        _TEXT_MATCH_WEIGHT * text_score
        + _TIME_OVERLAP_WEIGHT * time_score
        + _CENTER_ALIGN_WEIGHT * center_score
        + _LENGTH_MATCH_WEIGHT * length_score
        + _OCR_COVERAGE_WEIGHT * coverage_score
    )


def _token_similarity(ocr_line: str, span: list[TranscriptWord]) -> float:
    ocr_tokens = [_normalize(token) for token in ocr_line.split() if _normalize(token)]
    span_tokens = [_normalize(word.text) for word in span if _normalize(word.text)]
    if not ocr_tokens and not span_tokens:
        return 1.0
    if not ocr_tokens or not span_tokens:
        return 0.0
    return SequenceMatcher(None, ocr_tokens, span_tokens).ratio()


def _length_match_score(
    ocr_line: str,
    span: list[TranscriptWord],
    *,
    ocr_token_count: int,
) -> float:
    span_token_count = len(span)
    aligned_count = len(_aligned_whisper_words(ocr_line, span))
    if ocr_token_count <= 0:
        return 1.0
    if aligned_count < ocr_token_count:
        missing = ocr_token_count - aligned_count
        return 1.0 - min(1.0, missing / ocr_token_count)
    filler = max(0, span_token_count - aligned_count)
    return 1.0 - min(1.0, filler / max(ocr_token_count * 2, 1)) * 0.6


def _ocr_coverage_score(ocr_line: str, span: list[TranscriptWord]) -> float:
    ocr_tokens = [_normalize(token) for token in ocr_line.split() if _normalize(token)]
    if not ocr_tokens:
        return 1.0

    span_tokens = [_normalize(word.text) for word in span if _normalize(word.text)]
    if not span_tokens:
        return 0.0

    matcher = SequenceMatcher(None, ocr_tokens, span_tokens)
    covered = sum(
        i2 - i1 for tag, i1, i2, _j1, _j2 in matcher.get_opcodes() if tag in {"equal", "replace"}
    )
    return covered / len(ocr_tokens)


def _aligned_temporal_overlap_score(
    cue_start: float,
    cue_end: float,
    ocr_line: str,
    span: list[TranscriptWord],
) -> float:
    aligned_words = _aligned_whisper_words(ocr_line, span)
    if not aligned_words:
        return 0.0
    return _temporal_overlap_score(cue_start, cue_end, aligned_words)


def _aligned_whisper_words(
    ocr_line: str,
    span: list[TranscriptWord],
) -> list[TranscriptWord]:
    ocr_tokens = [_normalize(token) for token in ocr_line.split() if _normalize(token)]
    if not ocr_tokens or not span:
        return []

    span_tokens = [_normalize(word.text) for word in span]
    matcher = SequenceMatcher(None, ocr_tokens, span_tokens)
    aligned: list[TranscriptWord] = []
    for tag, _i1, _i2, j1, j2 in matcher.get_opcodes():
        if tag in {"equal", "replace"}:
            aligned.extend(span[j1:j2])
    return aligned


def _trim_filler_edges(ocr_line: str, span: list[TranscriptWord]) -> list[TranscriptWord]:
    """Drop leading/trailing Whisper words that do not align with OCR tokens."""
    if len(span) <= 1:
        return span

    ocr_tokens = [_normalize(token) for token in ocr_line.split() if _normalize(token)]
    if not ocr_tokens:
        return span

    span_tokens = [_normalize(word.text) for word in span]
    matcher = SequenceMatcher(None, ocr_tokens, span_tokens)
    first = 0
    last = len(span)
    for tag, _i1, _i2, j1, _j2 in matcher.get_opcodes():
        if tag in {"equal", "replace"}:
            first = j1
            break
    for tag, _i1, _i2, _j1, j2 in reversed(matcher.get_opcodes()):
        if tag in {"equal", "replace"}:
            last = j2
            break
    trimmed = span[first:last]
    return trimmed or span


def _temporal_overlap_score(cue_start: float, cue_end: float, words: list[TranscriptWord]) -> float:
    if not words:
        return 0.0
    span_start = float(words[0].start.seconds)
    span_end = float(words[-1].end.seconds)
    overlap_start = max(cue_start, span_start)
    overlap_end = min(cue_end, span_end)
    overlap = max(0.0, overlap_end - overlap_start)
    cue_len = max(0.01, cue_end - cue_start)
    return min(1.0, overlap / cue_len)


def _center_alignment_score(cue_start: float, cue_end: float, words: list[TranscriptWord]) -> float:
    cue_center = (cue_start + cue_end) / 2.0
    span_start = float(words[0].start.seconds)
    span_end = float(words[-1].end.seconds)
    span_center = (span_start + span_end) / 2.0
    max_delta = max(3.0, (cue_end - cue_start) * 2.0)
    return 1.0 - min(1.0, abs(cue_center - span_center) / max_delta)


def _fuse_line(ocr_line: str, whisper_words: list[TranscriptWord]) -> str:
    whisper_line = _join_whisper_words(whisper_words)
    if not whisper_line:
        return ocr_line

    if _similarity(_normalize(ocr_line), _normalize(whisper_line)) < _FULL_REPLACE_SIMILARITY:
        return _preserve_ocr_formatting(ocr_line, whisper_line)

    return _merge_tokens(ocr_line, whisper_words)


def _merge_tokens(ocr_line: str, whisper_words: list[TranscriptWord]) -> str:
    ocr_tokens = ocr_line.split()
    whisper_tokens = [word.text for word in whisper_words]
    if not ocr_tokens:
        return _join_whisper_words(whisper_words)
    if not whisper_tokens:
        return ocr_line

    matcher = SequenceMatcher(
        None,
        [_normalize(token) for token in ocr_tokens],
        [_normalize(token) for token in whisper_tokens],
    )

    merged: list[str] = []
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            merged.extend(ocr_tokens[i1:i2])
            continue
        if tag == "delete":
            merged.extend(ocr_tokens[i1:i2])
            continue
        if tag == "insert":
            merged.extend(whisper_tokens[j1:j2])
            continue

        ocr_span = ocr_tokens[i1:i2]
        whisper_span = whisper_tokens[j1:j2]
        whisper_probs = whisper_words[j1:j2]
        if len(ocr_span) == len(whisper_span):
            for ocr_token, whisper_token, whisper_word in zip(
                ocr_span, whisper_span, whisper_probs, strict=True
            ):
                merged.append(_choose_token(ocr_token, whisper_token, whisper_word.probability))
            continue

        if _span_confidence(whisper_probs) >= _MIN_WHISPER_WORD_PROBABILITY:
            merged.extend(whisper_span)
        else:
            merged.extend(ocr_span)

    return " ".join(merged)


def _choose_token(ocr_token: str, whisper_token: str, whisper_probability: float) -> str:
    if _normalize(ocr_token) == _normalize(whisper_token):
        return ocr_token
    if whisper_probability < _MIN_WHISPER_WORD_PROBABILITY:
        return ocr_token
    if _similarity(_normalize(ocr_token), _normalize(whisper_token)) >= _TOKEN_KEEP_OCR_SIMILARITY:
        return ocr_token
    return _preserve_token_punctuation(ocr_token, whisper_token)


def _preserve_ocr_formatting(ocr_line: str, whisper_line: str) -> str:
    result = whisper_line.strip()
    stripped_ocr = ocr_line.strip()

    if stripped_ocr.startswith(f"{_DIALOGUE_DASH} ") and not result.startswith(
        f"{_DIALOGUE_DASH} "
    ):
        result = f"{_DIALOGUE_DASH} {result.lstrip()}"

    if stripped_ocr.startswith(_MUSIC_NOTE) and not result.startswith(_MUSIC_NOTE):
        result = f"{_MUSIC_NOTE} {result.lstrip()}"

    if stripped_ocr.endswith(f" {_MUSIC_NOTE}") and not result.endswith(_MUSIC_NOTE):
        result = f"{result.rstrip()} {_MUSIC_NOTE}"

    if stripped_ocr.endswith(_MUSIC_NOTES) and not result.endswith(_MUSIC_NOTES):
        result = f"{result.rstrip()} {_MUSIC_NOTES}"

    return result


def _preserve_token_punctuation(ocr_token: str, whisper_token: str) -> str:
    prefix = ""
    suffix = ""
    core = whisper_token
    while core and not core[0].isalnum() and core[0] != "'":
        prefix += core[0]
        core = core[1:]
    while core and not core[-1].isalnum() and core[-1] != "'":
        suffix = core[-1] + suffix
        core = core[:-1]

    if _APOSTROPHE_RE.match(ocr_token) and not _APOSTROPHE_RE.match(prefix + core):
        prefix = "'" + prefix

    return f"{prefix}{core}{suffix}" if core else whisper_token


def _join_whisper_words(words: list[TranscriptWord]) -> str:
    return " ".join(word.text for word in words if word.text.strip())


def _span_confidence(words: list[TranscriptWord]) -> float:
    if not words:
        return 0.0
    return sum(word.probability for word in words) / len(words)


def _normalize(text: str) -> str:
    return _NORM_RE.sub("", text.casefold())


def _similarity(left: str, right: str) -> float:
    if not left and not right:
        return 1.0
    if not left or not right:
        return 0.0
    return SequenceMatcher(None, left, right).ratio()
