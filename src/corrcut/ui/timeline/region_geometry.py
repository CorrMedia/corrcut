"""Pure time-range math for timeline region drag/resize (unit-testable)."""

from __future__ import annotations

import bisect
from collections.abc import Iterable, Sequence

MIN_REGION_DURATION_S = 0.05


def snap_time(seconds: float, sample_rate: int | None, *, fine: bool = False) -> float:
    """Snap to one audio frame, or 10 ms when fine (Shift) is held."""
    if fine:
        step = 0.01
    elif sample_rate and sample_rate > 0:
        step = 1.0 / sample_rate
    else:
        step = 0.01
    return round(seconds / step) * step


PLAYHEAD_SNAP_PX = 10.0


class SortedSnapTargets(tuple[float, ...]):
    """Ascending snap times. Nearest-target search binary-searches this order."""


def sorted_snap_targets(times: Iterable[float]) -> SortedSnapTargets:
    """Unique ascending targets. Safe to reuse for the whole drag gesture."""
    return SortedSnapTargets(sorted(set(times)))


def _coerce_sorted(targets: Sequence[float]) -> Sequence[float]:
    if isinstance(targets, SortedSnapTargets):
        return targets
    return sorted_snap_targets(targets)


def nearest_sorted_target(
    value: float,
    targets: Sequence[float],
    threshold_s: float,
) -> tuple[float, float] | None:
    """Closest ``(distance, time)`` within ``threshold_s``.

    ``targets`` must be ascending. Equal distances keep the earlier time.
    """
    if threshold_s <= 0 or not targets:
        return None
    index = bisect.bisect_left(targets, value)
    best: tuple[float, float] | None = None
    for candidate_index in (index - 1, index):
        if candidate_index < 0 or candidate_index >= len(targets):
            continue
        target = float(targets[candidate_index])
        dist = abs(value - target)
        if dist > threshold_s:
            continue
        cand = (dist, target)
        if best is None or cand < best:
            best = cand
    return best


def playhead_snap_threshold_s(
    pixels_per_second: float,
    *,
    pixels: float = PLAYHEAD_SNAP_PX,
) -> float:
    """How close (in seconds) an edge must be to a snap target to magnet."""
    return float(pixels) / max(1.0, float(pixels_per_second))


def snap_moved_range_to_playhead(
    start_s: float,
    end_s: float,
    playhead_s: float,
    duration_s: float,
    threshold_s: float,
    *,
    min_duration_s: float = MIN_REGION_DURATION_S,
) -> tuple[float, float]:
    """Slide a moved region so its nearer edge sticks to the playhead.

    If both edges are in range, the out-point wins (aligns the end of the edit).
    """
    return snap_moved_range_to_targets(
        start_s,
        end_s,
        (playhead_s,),
        duration_s,
        threshold_s,
        min_duration_s=min_duration_s,
    )


def snap_moved_range_to_targets(
    start_s: float,
    end_s: float,
    targets: Sequence[float],
    duration_s: float,
    threshold_s: float,
    *,
    min_duration_s: float = MIN_REGION_DURATION_S,
) -> tuple[float, float]:
    """Slide a moved region so its nearer edge sticks to the closest target.

    If both edges are in range, the out-point wins (aligns the end of the edit).
    """
    if threshold_s <= 0 or not targets:
        return start_s, end_s
    ordered = _coerce_sorted(targets)
    length = end_s - start_s
    end_hit = nearest_sorted_target(end_s, ordered, threshold_s)
    start_hit = nearest_sorted_target(start_s, ordered, threshold_s)
    best: tuple[float, int] | None = None
    snapped_start = start_s
    snapped_end = end_s
    if end_hit is not None:
        best = (end_hit[0], 0)
        snapped_end = end_hit[1]
        snapped_start = end_hit[1] - length
    if start_hit is not None:
        cand = (start_hit[0], 1)
        if best is None or cand < best:
            best = cand
            snapped_start = start_hit[1]
            snapped_end = start_hit[1] + length
    if best is None:
        return start_s, end_s
    return apply_move(
        snapped_start,
        snapped_end,
        0.0,
        duration_s,
        min_duration_s=min_duration_s,
    )


def snap_resized_edge_to_playhead(
    start_s: float,
    end_s: float,
    playhead_s: float,
    duration_s: float,
    threshold_s: float,
    *,
    edge: str,
    min_duration_s: float = MIN_REGION_DURATION_S,
) -> tuple[float, float]:
    """Stick the trimmed in or out point to the playhead when close enough."""
    return snap_resized_edge_to_targets(
        start_s,
        end_s,
        (playhead_s,),
        duration_s,
        threshold_s,
        edge=edge,
        min_duration_s=min_duration_s,
    )


def snap_resized_edge_to_targets(
    start_s: float,
    end_s: float,
    targets: Sequence[float],
    duration_s: float,
    threshold_s: float,
    *,
    edge: str,
    min_duration_s: float = MIN_REGION_DURATION_S,
) -> tuple[float, float]:
    """Stick the trimmed in or out point to the closest target when close enough."""
    if threshold_s <= 0 or not targets:
        return start_s, end_s
    value = start_s if edge == "start" else end_s
    nearest = _nearest_target(value, targets, threshold_s)
    if nearest is None:
        return start_s, end_s
    if edge == "start":
        return apply_resize_left(
            start_s,
            end_s,
            nearest - start_s,
            duration_s,
            min_duration_s=min_duration_s,
        )
    return apply_resize_right(
        start_s,
        end_s,
        nearest - end_s,
        duration_s,
        min_duration_s=min_duration_s,
    )


def _nearest_target(
    value: float,
    targets: Sequence[float],
    threshold_s: float,
) -> float | None:
    hit = nearest_sorted_target(value, _coerce_sorted(targets), threshold_s)
    return None if hit is None else hit[1]


def apply_move(
    start_s: float,
    end_s: float,
    delta_s: float,
    duration_s: float,
    *,
    min_duration_s: float = MIN_REGION_DURATION_S,
) -> tuple[float, float]:
    """Slide region; preserve duration; clamp inside [0, duration]."""
    length = end_s - start_s
    start_s = max(0.0, start_s + delta_s)
    end_s = start_s + length
    if end_s > duration_s:
        end_s = duration_s
        start_s = max(0.0, end_s - length)
    if end_s - start_s < min_duration_s:
        end_s = min(duration_s, start_s + min_duration_s)
    return start_s, end_s


def apply_resize_left(
    start_s: float,
    end_s: float,
    delta_s: float,
    duration_s: float,
    *,
    min_duration_s: float = MIN_REGION_DURATION_S,
) -> tuple[float, float]:
    start_s = min(end_s - min_duration_s, max(0.0, start_s + delta_s))
    return start_s, end_s


def apply_resize_right(
    start_s: float,
    end_s: float,
    delta_s: float,
    duration_s: float,
    *,
    min_duration_s: float = MIN_REGION_DURATION_S,
) -> tuple[float, float]:
    end_s = max(start_s + min_duration_s, min(duration_s, end_s + delta_s))
    return start_s, end_s


def nudge_range(
    start_s: float,
    end_s: float,
    *,
    delta_start_s: float = 0.0,
    delta_end_s: float = 0.0,
    duration_s: float,
    min_duration_s: float = MIN_REGION_DURATION_S,
) -> tuple[float, float]:
    """Keyboard trim/slide: adjust one or both edges."""
    if delta_start_s != 0.0 and delta_end_s != 0.0:
        return apply_move(start_s, end_s, delta_start_s, duration_s, min_duration_s=min_duration_s)
    if delta_start_s != 0.0:
        return apply_resize_left(start_s, end_s, delta_start_s, duration_s, min_duration_s=min_duration_s)
    return apply_resize_right(start_s, end_s, delta_end_s, duration_s, min_duration_s=min_duration_s)
