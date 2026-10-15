"""Group Whisper transcript words into display cues."""

from __future__ import annotations

from fractions import Fraction

from corrcut.ai.transcript import TranscriptWord
from corrcut.domain.subtitle_cue import SubtitleCue, SubtitleSource

DEFAULT_MAX_GAP_SECONDS = Fraction(6, 10)  # 600 ms
DEFAULT_MAX_CHARS_PER_LINE = 42


def words_to_subtitle_cues(
    words: list[TranscriptWord],
    *,
    max_gap_seconds: Fraction = DEFAULT_MAX_GAP_SECONDS,
    max_chars_per_line: int = DEFAULT_MAX_CHARS_PER_LINE,
) -> list[SubtitleCue]:
    """Build subtitle cues from word-level timestamps."""
    if not words:
        return []

    groups: list[list[TranscriptWord]] = []
    current: list[TranscriptWord] = []

    for word in words:
        if not current:
            current.append(word)
            continue
        prev = current[-1]
        gap = word.start.seconds - prev.end.seconds
        same_segment = word.segment_text == prev.segment_text
        if gap <= max_gap_seconds and same_segment:
            current.append(word)
        else:
            groups.append(current)
            current = [word]
    if current:
        groups.append(current)

    cues: list[SubtitleCue] = []
    for group in groups:
        cues.extend(_group_to_cues(group, max_chars_per_line=max_chars_per_line))
    return cues


def _group_to_cues(
    group: list[TranscriptWord],
    *,
    max_chars_per_line: int,
) -> list[SubtitleCue]:
    if not group:
        return []

    lines: list[tuple[list[TranscriptWord], str]] = []
    line_words: list[TranscriptWord] = []
    line_len = 0

    for word in group:
        token = word.text.strip()
        if not token:
            continue
        extra = len(token) + (1 if line_len else 0)
        if line_words and line_len + extra > max_chars_per_line:
            lines.append((line_words, " ".join(w.text for w in line_words)))
            line_words = [word]
            line_len = len(token)
        else:
            line_words.append(word)
            line_len += extra
    if line_words:
        lines.append((line_words, " ".join(w.text for w in line_words)))

    if not lines:
        return []

    if len(lines) == 1:
        words_row, text = lines[0]
        return [
            SubtitleCue.create(
                start=words_row[0].start,
                end=words_row[-1].end,
                text=text,
                source=SubtitleSource.WHISPER,
            )
        ]

    total_words = sum(len(row[0]) for row in lines)
    duration = group[-1].end.seconds - group[0].start.seconds
    if duration <= 0:
        duration = Fraction(1, 1)

    cues: list[SubtitleCue] = []
    cursor = group[0].start.seconds
    for words_row, text in lines:
        share = Fraction(len(words_row), max(1, total_words))
        end = cursor + duration * share
        cues.append(
            SubtitleCue.create(
                start=words_row[0].start,
                end=words_row[-1].end if len(words_row) > 1 else words_row[0].end,
                text=text,
                source=SubtitleSource.WHISPER,
            )
        )
        cursor = end
    return cues
