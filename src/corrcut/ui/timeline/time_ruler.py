"""Zoom-adaptive major/minor tick intervals and compact ruler labels (no Qt)."""

from __future__ import annotations

import bisect
from collections.abc import Sequence

# Preferred major intervals (seconds), coarsest last.
_MAJOR_INTERVALS: tuple[float, ...] = (
    0.1,
    0.2,
    0.5,
    1.0,
    2.0,
    5.0,
    10.0,
    15.0,
    30.0,
    60.0,
    120.0,
    300.0,
    600.0,
)

# Target spacing between major labels in pixels (HH:MM:SS.cc is wide).
_MIN_MAJOR_PX = 96.0


def major_interval_for_pps(pps: float) -> float:
    """Pick a major tick interval so labels stay roughly 80–120px apart."""
    pps = max(1e-6, float(pps))
    for interval in _MAJOR_INTERVALS:
        if interval * pps >= _MIN_MAJOR_PX:
            return interval
    return _MAJOR_INTERVALS[-1]


def minor_interval_for_major(major_s: float) -> float:
    """Minor ticks at 1/5 of major (all planned majors divide evenly by 5)."""
    return float(major_s) / 5.0


def format_ruler_label(seconds: float) -> str:
    """Zero-padded HH:MM:SS.cc (centiseconds) for the timeline ticks bar."""
    seconds = max(0.0, float(seconds))
    total_cs = int(round(seconds * 100.0))
    cs = total_cs % 100
    total_s = total_cs // 100
    s = total_s % 60
    total_m = total_s // 60
    m = total_m % 60
    h = total_m // 60
    return f"{h:02d}:{m:02d}:{s:02d}.{cs:02d}"


def iter_ticks(
    *,
    scroll_x: float,
    viewport_width: float,
    pps: float,
    duration: float,
) -> list[tuple[float, bool]]:
    """
    Visible ticks as (time_s, is_major) within the viewport, clamped to [0, duration].

    Includes a small overscan so edge ticks paint cleanly.
    """
    pps = max(1e-6, float(pps))
    major = major_interval_for_pps(pps)
    minor = minor_interval_for_major(major)
    left_t = max(0.0, (scroll_x - 2.0) / pps)
    right_t = min(duration, (scroll_x + viewport_width + 2.0) / pps)
    if right_t < left_t:
        return []

    first = int(left_t / minor) * minor
    if first < left_t - 1e-9:
        first += minor

    ticks: list[tuple[float, bool]] = []
    t = first
    n = 0
    max_n = int((right_t - left_t) / minor) + 8
    while t <= right_t + 1e-9 and n < max_n:
        if 0.0 <= t <= duration + 1e-9:
            nearest_major = round(t / major) * major
            is_major = abs(t - nearest_major) < minor * 0.01
            ticks.append((t, is_major))
        t += minor
        n += 1
    return ticks


# Same edge overscan as ``iter_ticks`` so a mark on the viewport rim still paints.
_MARK_OVERSCAN_PX = 2.0


def visible_scene_cuts(
    times: Sequence[float],
    *,
    scroll_x: float,
    viewport_width: float,
    pps: float,
) -> Sequence[float]:
    """Cut times whose marks fall in the viewport.

    ``times`` must be ascending. Playback repaints the ruler on every playhead
    tick, so this binary-searches the visible window instead of scanning the
    whole list.
    """
    if not times:
        return ()
    pps = max(1e-6, float(pps))
    left_t = (float(scroll_x) - _MARK_OVERSCAN_PX) / pps
    right_t = (float(scroll_x) + float(viewport_width) + _MARK_OVERSCAN_PX) / pps
    if right_t < left_t:
        return ()
    start = bisect.bisect_left(times, left_t)
    end = bisect.bisect_right(times, right_t)
    if start >= end:
        return ()
    return times[start:end]
