"""Subtitle display timing."""

from __future__ import annotations

from corrcut.domain.subtitle_cue import SubtitleCue, SubtitleSource
from corrcut.domain.time_point import TimePoint
from corrcut.engine.subtitles.display import (
    DEFAULT_POST_DISPLAY_SECONDS,
    SubtitleCueCursor,
    cue_at_seconds,
)


def _cue(start: float, end: float, text: str = "line") -> SubtitleCue:
    rate = 48_000
    return SubtitleCue.create(
        start=TimePoint.from_seconds_float(start, rate),
        end=TimePoint.from_seconds_float(end, rate),
        text=text,
        source=SubtitleSource.EMBEDDED,
    )


def test_active_cue_within_window() -> None:
    cues = [_cue(1.0, 2.0, "one")]
    assert cue_at_seconds(cues, 1.5) is cues[0]


def test_lingers_briefly_after_cue_end() -> None:
    cues = [_cue(1.0, 2.0, "one"), _cue(5.0, 6.0, "two")]
    assert cue_at_seconds(cues, 2.5) is cues[0]
    assert cue_at_seconds(cues, 2.0 + DEFAULT_POST_DISPLAY_SECONDS - 0.1) is cues[0]


def test_clears_after_linger_expires_before_next_cue() -> None:
    cues = [_cue(1.0, 2.0, "one"), _cue(5.0, 6.0, "two")]
    assert cue_at_seconds(cues, 2.0 + DEFAULT_POST_DISPLAY_SECONDS + 0.1) is None
    assert cue_at_seconds(cues, 4.9) is None


def test_switches_at_next_cue_start() -> None:
    cues = [_cue(1.0, 2.0, "one"), _cue(5.0, 6.0, "two")]
    assert cue_at_seconds(cues, 5.0) is cues[1]


def test_active_cue_takes_priority_over_previous_linger() -> None:
    cues = [_cue(1.0, 2.0, "one"), _cue(2.5, 4.0, "two")]
    assert cue_at_seconds(cues, 2.6) is cues[1]


def test_clears_after_last_cue_linger() -> None:
    cues = [_cue(1.0, 2.0, "one")]
    assert cue_at_seconds(cues, 2.0 + DEFAULT_POST_DISPLAY_SECONDS - 0.1) is cues[0]
    assert cue_at_seconds(cues, 2.0 + DEFAULT_POST_DISPLAY_SECONDS + 0.1) is None


def test_clears_before_first_cue() -> None:
    cues = [_cue(1.0, 2.0, "one")]
    assert cue_at_seconds(cues, 0.5) is None


def test_bisect_finds_cue_in_long_list() -> None:
    cues = [_cue(float(i), float(i) + 0.4, str(i)) for i in range(400)]
    assert cue_at_seconds(cues, 250.2) is cues[250]
    assert cue_at_seconds(cues, 250.7) is cues[250]
    assert cue_at_seconds(cues, 249.9) is cues[249]


def test_overlapping_active_cue_prefers_earlier() -> None:
    cues = [_cue(1.0, 4.0, "one"), _cue(2.0, 3.0, "two")]
    assert cue_at_seconds(cues, 2.5) is cues[0]
    assert cue_at_seconds(cues, 3.5) is cues[0]
    assert cue_at_seconds(cues, 4.1) is cues[1]


def test_cursor_keeps_cue_until_playback_leaves_window() -> None:
    cues = [_cue(float(i), float(i) + 0.4, str(i)) for i in range(30)]
    cursor = SubtitleCueCursor()
    held = cursor.cue_at(cues, 10.1)
    assert held is cues[10]
    cues[10] = _cue(10.0, 10.4, "changed")
    assert cursor.holds(cues, 10.6)
    assert cursor.cue_at(cues, 10.6) is held
    assert cursor.cue_at(cues, 9.2) is cues[9]
    assert cursor.cue_at(cues, 11.0) is cues[11]
