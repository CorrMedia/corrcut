"""Profanity lexicon matching."""

from fractions import Fraction

from corrcut.ai.detect import detect_suggestions
from corrcut.ai.lexicon import DEFAULT_LEXICON, normalize_token
from corrcut.ai.transcript import TranscriptWord
from corrcut.domain.ai_suggestion import DetectionSettings, SuggestionStatus
from corrcut.domain.time_point import TimePoint


def test_normalize_strips_punctuation() -> None:
    assert normalize_token("Shit!") == "shit"


def test_lexicon_matches_stem_variant() -> None:
    match = DEFAULT_LEXICON.match("fucks")
    assert match is not None
    assert match.term == "fuck"


def test_lexicon_does_not_match_common_word_an() -> None:
    """'anus' variant generation must not flag the article 'an'."""
    assert DEFAULT_LEXICON.match("an") is None
    assert DEFAULT_LEXICON.match("anus") is not None


def test_lexicon_matches_leet_speak() -> None:
    match = DEFAULT_LEXICON.match("sh1t")
    assert match is not None
    assert match.term == "shit"


def test_lexicon_matches_sexual_list_term() -> None:
    match = DEFAULT_LEXICON.match("tit")
    assert match is not None
    assert match.term == "tit"
    assert match.category == "sexual"


def test_lexicon_maps_expansion_to_canonical_key() -> None:
    match = DEFAULT_LEXICON.match("clitoris")
    assert match is not None
    assert match.term == "clit"


def test_detect_builds_padded_window() -> None:
    rate = 48000
    words = [
        TranscriptWord(
            text="well",
            start=TimePoint.from_seconds_float(0.0, rate),
            end=TimePoint.from_seconds_float(0.2, rate),
            probability=0.95,
            segment_text="well shit",
        ),
        TranscriptWord(
            text="shit",
            start=TimePoint.from_seconds_float(0.25, rate),
            end=TimePoint.from_seconds_float(0.45, rate),
            probability=0.9,
            segment_text="well shit",
        ),
    ]
    settings = DetectionSettings(pre_padding_ms=100, post_padding_ms=100)
    hits = detect_suggestions(words, stream_index=1, settings=settings, duration_seconds=2.0)
    assert len(hits) == 1
    hit = hits[0]
    assert hit.matched_term == "shit"
    assert "well shit" in hit.transcript_phrase
    assert hit.time_range.start.seconds < TimePoint.from_seconds_float(0.25, rate).seconds
    assert hit.status == SuggestionStatus.PENDING


def test_detect_adds_extra_padding_when_whisper_confidence_low() -> None:
    words = [
        TranscriptWord(
            text="damn",
            start=TimePoint(Fraction(1)),
            end=TimePoint(Fraction(6, 5)),
            probability=0.2,
            segment_text="damn",
        )
    ]
    settings = DetectionSettings(
        pre_padding_ms=100,
        post_padding_ms=100,
        low_confidence_extra_padding_ms=200,
        whisper_word_confidence_cutoff=0.55,
    )
    hits = detect_suggestions(words, stream_index=0, settings=settings)
    pad = hits[0].time_range.start.seconds
    assert pad == Fraction(7, 10)  # 1.0s - 300ms padding
