"""Timed table helper utilities."""

from __future__ import annotations

from corrcut.engine.subtitles.display import DEFAULT_POST_DISPLAY_SECONDS
from corrcut.ui.table_helpers import timed_item_at_seconds, timed_item_for_playhead


class _Timed:
    def __init__(self, start: float, end: float, label: str) -> None:
        self.start = start
        self.end = end
        self.label = label


def test_timed_item_at_seconds_returns_first_matching_range() -> None:
    items = [_Timed(0.0, 1.0, "a"), _Timed(2.0, 3.0, "b")]
    match = timed_item_at_seconds(
        items,
        2.5,
        start_seconds=lambda item: item.start,
        end_seconds=lambda item: item.end,
    )
    assert match is not None
    assert match.label == "b"


def test_timed_item_at_seconds_returns_none_outside_ranges() -> None:
    items = [_Timed(1.0, 2.0, "a")]
    assert (
        timed_item_at_seconds(
            items,
            2.0,
            start_seconds=lambda item: item.start,
            end_seconds=lambda item: item.end,
        )
        is None
    )


def test_timed_item_for_playhead_lingers_after_end() -> None:
    items = [_Timed(1.0, 2.0, "a"), _Timed(4.0, 5.0, "b")]
    match = timed_item_for_playhead(
        items,
        2.0 + DEFAULT_POST_DISPLAY_SECONDS - 0.1,
        start_seconds=lambda item: item.start,
        end_seconds=lambda item: item.end,
    )
    assert match is not None
    assert match.label == "a"


def test_timed_item_for_playhead_prefers_active_item_over_linger() -> None:
    items = [_Timed(1.0, 2.0, "a"), _Timed(2.5, 3.5, "b")]
    match = timed_item_for_playhead(
        items,
        2.6,
        start_seconds=lambda item: item.start,
        end_seconds=lambda item: item.end,
    )
    assert match is not None
    assert match.label == "b"
