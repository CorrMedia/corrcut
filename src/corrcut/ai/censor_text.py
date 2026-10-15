"""Censor profanity in plain text using the swear lexicon."""

from __future__ import annotations

import re

from corrcut.ai.detect import _find_phrase_hits
from corrcut.ai.normalize import normalize_token
from corrcut.ai.swear_lexicon import SwearLexicon
from corrcut.ai.transcript import TranscriptWord
from corrcut.domain.time_point import TimePoint

_TOKEN_SPAN_RE = re.compile(r"[^\W_]+", re.UNICODE)


def _token_spans(text: str) -> list[tuple[int, int, str]]:
    return [(m.start(), m.end(), m.group()) for m in _TOKEN_SPAN_RE.finditer(text)]


def _pseudo_words(tokens: list[str]) -> list[TranscriptWord]:
    """Minimal TranscriptWord rows for phrase matching (timestamps unused)."""
    return [
        TranscriptWord(
            text=token,
            start=TimePoint.from_seconds_float(0.0, 48_000),
            end=TimePoint.from_seconds_float(0.1, 48_000),
            probability=1.0,
            segment_text=token,
        )
        for token in tokens
    ]


def censor_text(
    text: str,
    *,
    lexicon: SwearLexicon | None = None,
    track_language: str | None = None,
    replacement: str = "***",
) -> str:
    """
    Replace profane words/phrases in ``text`` with ``replacement``.

    Preserves non-token punctuation and spacing between censored spans.
    """
    if not text.strip():
        return text

    swear_lexicon = lexicon or SwearLexicon.build(track_language=track_language)
    spans = _token_spans(text)
    if not spans:
        return text

    tokens = [span[2] for span in spans]
    word_norms = [normalize_token(token) for token in tokens]
    pseudo = _pseudo_words(tokens)

    censored_indices: set[int] = set()
    phrase_hits, consumed = _find_phrase_hits(pseudo, word_norms, swear_lexicon.phrases)
    for mute_start, mute_end, _pattern, _prob in phrase_hits:
        for index in range(mute_start, mute_end + 1):
            censored_indices.add(index)

    for index, token in enumerate(tokens):
        if index in consumed:
            continue
        if swear_lexicon.words.match(token, word_norms=word_norms, word_index=index):
            censored_indices.add(index)

    if not censored_indices:
        return text

    repl = replacement if replacement else "***"
    parts: list[str] = []
    cursor = 0
    for index, (start, end, _token) in enumerate(spans):
        if start > cursor:
            parts.append(text[cursor:start])
        if index in censored_indices:
            parts.append(repl)
        else:
            parts.append(text[start:end])
        cursor = end
    if cursor < len(text):
        parts.append(text[cursor:])
    return "".join(parts)


def contains_profanity(
    text: str,
    *,
    lexicon: SwearLexicon | None = None,
    track_language: str | None = None,
) -> bool:
    """Return True when ``text`` contains a lexicon profanity word or phrase."""
    if not text.strip():
        return False

    swear_lexicon = lexicon or SwearLexicon.build(track_language=track_language)
    spans = _token_spans(text)
    if not spans:
        return False

    tokens = [span[2] for span in spans]
    word_norms = [normalize_token(token) for token in tokens]
    pseudo = _pseudo_words(tokens)

    phrase_hits, consumed = _find_phrase_hits(pseudo, word_norms, swear_lexicon.phrases)
    if phrase_hits:
        return True

    for index, token in enumerate(tokens):
        if index in consumed:
            continue
        if swear_lexicon.words.match(token, word_norms=word_norms, word_index=index):
            return True
    return False


def censor_subtitle_line(
    text: str,
    *,
    lexicon: SwearLexicon | None = None,
    track_language: str | None = None,
    replacement: str = "***",
) -> str:
    """Censor each line of a multi-line subtitle cue."""
    lines = text.splitlines()
    if not lines:
        return censor_text(
            text,
            lexicon=lexicon,
            track_language=track_language,
            replacement=replacement,
        )
    return "\n".join(
        censor_text(
            line,
            lexicon=lexicon,
            track_language=track_language,
            replacement=replacement,
        )
        for line in lines
    )
