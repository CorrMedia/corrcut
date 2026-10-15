"""Skip preview: jump over enabled skip ranges on the source timeline."""

from __future__ import annotations

from collections.abc import Sequence

from corrcut.domain.operation import Operation, OperationType
from corrcut.domain.project import Project
from corrcut.preview.playback_profile import PlaybackProfile

# Contiguous / overlapping skips within this gap chain into one jump.
_CHAIN_GAP_S = 1e-3


def skip_ranges_from_operations(operations: Sequence[Operation]) -> list[tuple[float, float]]:
    """Enabled skip ranges as ``(start, end)`` in ascending start order."""
    ranges: list[tuple[float, float]] = []
    for op in operations:
        if not op.enabled or op.type != OperationType.SKIP:
            continue
        start = float(op.time_range.start.seconds)
        end = float(op.time_range.end.seconds)
        if end > start:
            ranges.append((start, end))
    ranges.sort(key=lambda pair: pair[0])
    return ranges


def skip_ranges_from_project(
    project: Project | None,
    *,
    profile: PlaybackProfile | None = None,
) -> list[tuple[float, float]]:
    if project is None:
        return []
    operations = project.operations
    if profile is not None:
        operations = [op for op in operations if profile.matches(op)]
    return skip_ranges_from_operations(operations)


def skip_exit_seconds(
    seconds: float,
    ranges: Sequence[tuple[float, float]],
    *,
    chain_gap_s: float = _CHAIN_GAP_S,
) -> float | None:
    """Return the end of the contiguous skip block containing ``seconds``.

    Half-open ranges: ``start <= t < end``. Overlapping or abutting skips
    (gap <= ``chain_gap_s``) chain into one exit. Returns ``None`` when ``t``
    is not inside any skip.
    """
    t = float(seconds)
    if not ranges:
        return None

    containing: tuple[float, float] | None = None
    for start, end in ranges:
        if start <= t < end:
            containing = (start, end)
            break
    if containing is None:
        return None

    exit_s = containing[1]
    # Advance through later ranges that overlap or abut the current exit.
    for start, end in ranges:
        if start > exit_s + chain_gap_s:
            break
        if end > exit_s and start <= exit_s + chain_gap_s:
            exit_s = end
    if exit_s <= t:
        return None
    return exit_s


# Land just before a skip's start so the half-open range does not contain it.
_REWIND_CLEARANCE_S = 1e-3


def skip_rewind_seconds(
    seconds: float,
    ranges: Sequence[tuple[float, float]],
    *,
    chain_gap_s: float = _CHAIN_GAP_S,
) -> float | None:
    """Return a time just before the contiguous skip block containing ``seconds``.

    Used when playback is moving backward: jump to the earlier edge instead of
    the end. Returns ``None`` when ``t`` is not inside any skip. A block that
    starts at 0 lands on 0, which is still inside that skip — the caller should
    pause rather than seek again.
    """
    t = float(seconds)
    if not ranges:
        return None

    index: int | None = None
    for i, (start, end) in enumerate(ranges):
        if start <= t < end:
            index = i
            break
    if index is None:
        return None

    entry_s = ranges[index][0]
    for start, end in reversed(ranges[:index]):
        if end < entry_s - chain_gap_s:
            break
        if start < entry_s:
            entry_s = start
    return max(0.0, entry_s - _REWIND_CLEARANCE_S)
