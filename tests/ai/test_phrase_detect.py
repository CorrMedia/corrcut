"""Phrase-level and language-aware lexicon detection."""

from corrcut.ai.detect import detect_suggestions
from corrcut.ai.lexicon import ProfanityLexicon
from corrcut.ai.normalize import normalize_token
from corrcut.ai.swear_lexicon import SwearLexicon
from corrcut.ai.swears_loader import (
    SwearEntry,
    entry_applies_to_language,
    filter_entries_by_language,
    load_swear_entries,
    repo_swears_dir,
)
from corrcut.ai.transcript import TranscriptWord
from corrcut.domain.ai_suggestion import DetectionSettings
from corrcut.domain.time_point import TimePoint


def _word(text: str, start: float, end: float) -> TranscriptWord:
    rate = 48000
    return TranscriptWord(
        text=text,
        start=TimePoint.from_seconds_float(start, rate),
        end=TimePoint.from_seconds_float(end, rate),
        probability=0.9,
        segment_text=text,
    )


def test_you_not_registered_as_fuck_alias() -> None:
    lex = ProfanityLexicon.from_swears_dir(repo_swears_dir())
    assert lex.match("you") is None


def test_exception_uses_token_sequence() -> None:
    lex = ProfanityLexicon.from_swears_dir(repo_swears_dir())
    norms = [normalize_token(w) for w in ["he", "hit", "the", "golf", "balls"]]
    idx = norms.index("balls")
    assert lex.match("balls", word_norms=norms, word_index=idx) is None


def test_phrase_fuck_you_mutes_both_when_filter_constituents_true() -> None:
    words = [
        _word("well", 0.0, 0.2),
        _word("fuck", 0.25, 0.4),
        _word("you", 0.45, 0.6),
    ]
    hits = detect_suggestions(words, stream_index=0, settings=DetectionSettings())
    assert len(hits) == 1
    span = hits[0].time_range
    assert span.start.seconds < TimePoint.from_seconds_float(0.25, 48000).seconds
    assert span.end.seconds > TimePoint.from_seconds_float(0.6, 48000).seconds
    assert hits[0].matched_term == "fuck"
    assert hits[0].category == "profanity"


def test_golf_balls_phrase_exception() -> None:
    words = [
        _word("he", 0.0, 0.1),
        _word("hit", 0.12, 0.2),
        _word("the", 0.22, 0.28),
        _word("golf", 0.3, 0.45),
        _word("balls", 0.46, 0.6),
    ]
    hits = detect_suggestions(words, stream_index=0, settings=DetectionSettings())
    assert hits == []


def test_language_filter_skips_mismatched_entry() -> None:
    entry = SwearEntry(
        key="scheisse",
        category="profanity",
        languages=frozenset({"de"}),
        plural=False,
        forms=(),
        phrases=(),
        exceptions=frozenset(),
    )
    assert entry_applies_to_language(entry, "en") is False
    assert entry_applies_to_language(entry, "de") is True
    assert entry_applies_to_language(entry, "de-DE") is True

    entries = load_swear_entries(repo_swears_dir())
    english = filter_entries_by_language(entries, "en")
    assert all(entry_applies_to_language(e, "en") or not e.languages for e in english)


def test_plural_inflection_pussies() -> None:
    entries = [e for e in load_swear_entries(repo_swears_dir()) if e.key == "pussy"]
    assert entries
    lex = SwearLexicon.build(track_language="en").words
    match = lex.match("pussies")
    assert match is not None
    assert match.term == "pussy"
