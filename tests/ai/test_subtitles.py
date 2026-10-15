"""Subtitle parse and stream selection."""

from fractions import Fraction

from corrcut.ai.subtitles import (
    SubtitleCue,
    parse_srt,
    select_subtitle_stream_index,
    tokenize_cue_text,
    words_from_cues,
)
from corrcut.domain.time_point import TimePoint

SAMPLE_SRT = """1
00:00:01,000 --> 00:00:03,500
What the <i>fuck</i> is this?

2
00:00:10,000 --> 00:00:11,000
Damn.
"""


def test_parse_srt_cues() -> None:
    cues = parse_srt(SAMPLE_SRT)
    assert len(cues) == 2
    assert cues[0].start == Fraction(1)
    assert cues[0].end == Fraction(7, 2)
    assert "fuck" in cues[0].text


def test_tokenize_strips_tags() -> None:
    assert tokenize_cue_text("What the <b>hell</b>") == ["What", "the", "hell"]


def test_words_from_cues_interpolates_times() -> None:
    cues = [SubtitleCue(start=Fraction(0), end=Fraction(2), text="oh shit")]
    words = words_from_cues(cues, sample_rate=48000)
    assert len(words) == 2
    assert words[0].text == "oh"
    assert words[1].text == "shit"
    assert words[0].start.seconds < words[1].start.seconds
    assert words[1].end.seconds == TimePoint.from_seconds_float(2.0, 48000).seconds


def test_select_subtitle_prefers_language_match() -> None:
    streams = [
        {"index": 6, "codec": "dvd_subtitle", "language": "eng"},
        {"index": 9, "codec": "subrip", "language": "eng"},
        {"index": 10, "codec": "subrip", "language": "spa"},
    ]
    assert select_subtitle_stream_index(streams, preferred_index=None, audio_language="eng") == 9


def test_select_subtitle_skips_bitmap() -> None:
    streams = [{"index": 6, "codec": "dvd_subtitle", "language": "eng"}]
    assert select_subtitle_stream_index(streams, preferred_index=None, audio_language="eng") is None
