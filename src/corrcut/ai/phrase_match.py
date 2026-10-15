"""Phrase-level matching and exception checks for AI detection."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

from corrcut.ai.normalize import normalize_token

_TOKEN_SPLIT = re.compile(r"[\s\-_/]+")

MuteSpan = Literal["phrase", "trigger"]


@dataclass(frozen=True)
class PhrasePattern:
    """Multi-word pattern loaded from swear JSON."""

    tokens: tuple[str, ...]
    canonical: str
    category: str
    mute_span: MuteSpan
    exceptions: frozenset[str]
    raw: str


def tokenize_phrase(text: str) -> tuple[str, ...]:
    parts = [
        normalize_token(part)
        for part in _TOKEN_SPLIT.split(text.strip())
        if part.strip()
    ]
    return tuple(part for part in parts if len(part) >= 1)


def exception_phrase_tokens(exception: str) -> tuple[str, ...]:
    return tokenize_phrase(exception.lower())


def phrase_tokens_match(window: list[str], pattern: tuple[str, ...]) -> bool:
    if len(window) != len(pattern):
        return False
    return window == list(pattern)


def find_exception_in_words(
    exception: str,
    word_norms: list[str],
    *,
    center_index: int,
    radius: int = 6,
) -> bool:
    """True when normalized exception tokens appear consecutively near *center_index*."""
    pattern = exception_phrase_tokens(exception)
    if not pattern:
        return False
    lo = max(0, center_index - radius)
    hi = min(len(word_norms), center_index + radius + 1)
    window = word_norms[lo:hi]
    width = len(pattern)
    for start in range(len(window) - width + 1):
        if phrase_tokens_match(window[start : start + width], pattern):
            return True
    return False


def is_blocked_by_exceptions(
    word_norms: list[str],
    center_index: int,
    exceptions: frozenset[str],
    *,
    radius: int = 6,
) -> bool:
    if not exceptions:
        return False
    for exc in exceptions:
        if find_exception_in_words(exc, word_norms, center_index=center_index, radius=radius):
            return True
    return False
