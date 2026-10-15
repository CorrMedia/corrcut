"""Logarithmic zoom slider mapping (typical NLE time-scale control)."""

from __future__ import annotations

import math

# Tune these to change how far the timeline can zoom in/out.
# Lower values allow zooming out to see more time on screen.
MIN_PIXELS_PER_SECOND = 1.0
MAX_PIXELS_PER_SECOND = 400.0
# Also cap zoom so at least this many seconds stay visible (depends on viewport width).
MIN_TIMELINE_VISIBLE_SECONDS_AT_MAX_ZOOM = 1.0
SLIDER_STEPS = 1000


def max_pixels_per_second(viewport_width: float | None = None) -> float:
    """Upper zoom limit; optional viewport keeps ~1s of timeline visible at max zoom."""
    cap = MAX_PIXELS_PER_SECOND
    if viewport_width is not None and viewport_width > 0:
        cap = min(cap, viewport_width / MIN_TIMELINE_VISIBLE_SECONDS_AT_MAX_ZOOM)
    return max(MIN_PIXELS_PER_SECOND, cap)


def clamp_pixels_per_second(pps: float, viewport_width: float | None = None) -> float:
    return max(MIN_PIXELS_PER_SECOND, min(max_pixels_per_second(viewport_width), pps))


def pixels_per_second_from_slider(slider_value: int, viewport_width: float | None = None) -> float:
    t = max(0, min(SLIDER_STEPS, slider_value)) / float(SLIDER_STEPS)
    pmin = MIN_PIXELS_PER_SECOND
    pmax = max_pixels_per_second(viewport_width)
    return pmin * ((pmax / pmin) ** t)


def slider_from_pixels_per_second(pps: float, viewport_width: float | None = None) -> int:
    pps = clamp_pixels_per_second(pps, viewport_width)
    pmin = MIN_PIXELS_PER_SECOND
    pmax = max_pixels_per_second(viewport_width)
    t = math.log(pps / pmin) / math.log(pmax / pmin)
    return int(round(t * SLIDER_STEPS))
