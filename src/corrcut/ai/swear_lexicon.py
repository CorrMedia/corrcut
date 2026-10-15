"""Bundled word + phrase lexicon for detection."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from corrcut.ai.lexicon import ProfanityLexicon
from corrcut.ai.phrase_match import PhrasePattern
from corrcut.ai.swears_loader import (
    collect_phrase_patterns,
    filter_entries_by_language,
    load_swear_entries,
    resolve_swears_dir,
)


@dataclass(frozen=True)
class SwearLexicon:
    """Word-level lexicon plus ordered phrase patterns for a track language."""

    words: ProfanityLexicon
    phrases: tuple[PhrasePattern, ...]

    @classmethod
    def build(
        cls,
        *,
        track_language: str | None = None,
        swears_dir: Path | None = None,
    ) -> SwearLexicon:
        directory = swears_dir or resolve_swears_dir()
        entries = load_swear_entries(directory)
        filtered = filter_entries_by_language(entries, track_language)
        return cls(
            words=ProfanityLexicon(swear_entries=filtered),
            phrases=collect_phrase_patterns(filtered),
        )


def default_swear_lexicon() -> SwearLexicon:
    return SwearLexicon.build(track_language=None)
