"""Token normalization for lexicon matching and suggestion display."""

from __future__ import annotations

import re
import unicodedata

_LEET = str.maketrans(
    {
        "0": "o",
        "1": "i",
        "3": "e",
        "4": "a",
        "5": "s",
        "@": "a",
        "$": "s",
    }
)


def _collapse_repeated_letters(text: str) -> str:
    return re.sub(r"(.)\1{2,}", r"\1\1", text)


# Sentence punctuation is stripped for display. Hyphens and apostrophes stay.
_DISPLAY_PUNCT_KEEP = frozenset("-'’‘ʼ‐‑")


def strip_display_punctuation(text: str) -> str:
    """Remove periods, commas, and other sentence punctuation.

    Hyphens and apostrophes are part of the word (``well-known``, ``fuckin'``).
    """
    kept = [
        ch
        for ch in text
        if ch in _DISPLAY_PUNCT_KEEP or not unicodedata.category(ch).startswith("P")
    ]
    return re.sub(r"\s+", " ", "".join(kept)).strip()


def normalize_token(text: str) -> str:
    """Lowercase, strip punctuation, leet-speak, collapse repeats."""
    token = text.lower().translate(_LEET)
    token = re.sub(r"[^a-z']+", "", token)
    token = _collapse_repeated_letters(token)
    return token
