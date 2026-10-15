"""Bitmap subtitle codec selection and project-cue fallback."""

from corrcut.ai.subtitles import (
    select_bitmap_subtitle_stream_index,
    select_subtitle_stream_index,
    words_from_project_cues,
)
from corrcut.domain.time_point import TimePoint


def test_select_bitmap_subtitle_prefers_language_match() -> None:
    streams = [
        {"index": 3, "codec": "hdmv_pgs_subtitle", "language": "eng"},
        {"index": 4, "codec": "hdmv_pgs_subtitle", "language": "spa"},
    ]
    assert (
        select_bitmap_subtitle_stream_index(
            streams,
            preferred_index=None,
            audio_language="eng",
        )
        == 3
    )


def test_select_bitmap_subtitle_prefers_pgs_over_dvd() -> None:
    streams = [
        {"index": 5, "codec": "dvd_subtitle", "language": "eng"},
        {"index": 3, "codec": "hdmv_pgs_subtitle", "language": "eng"},
    ]
    assert (
        select_bitmap_subtitle_stream_index(
            streams,
            preferred_index=None,
            audio_language="eng",
        )
        == 3
    )


def test_select_bitmap_subtitle_includes_dvd() -> None:
    streams = [{"index": 6, "codec": "dvd_subtitle", "language": "eng"}]
    assert (
        select_bitmap_subtitle_stream_index(
            streams,
            preferred_index=None,
            audio_language="eng",
            ocr_only=True,
        )
        == 6
    )


def test_words_from_project_cues() -> None:
    cues = [
        {
            "text": "What the fuck",
            "start": TimePoint.from_seconds_float(1.0, 48_000).to_json(),
            "end": TimePoint.from_seconds_float(3.0, 48_000).to_json(),
        }
    ]
    words = words_from_project_cues(cues, sample_rate=48_000)
    assert [w.text for w in words] == ["What", "the", "fuck"]
    assert words[0].start.seconds < words[-1].end.seconds


def test_select_text_still_skips_bitmap() -> None:
    streams = [{"index": 6, "codec": "hdmv_pgs_subtitle", "language": "eng"}]
    assert select_subtitle_stream_index(streams, preferred_index=None, audio_language="eng") is None
