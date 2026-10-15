"""Unit tests for preview skip seek math."""

from fractions import Fraction

from corrcut.domain.operation import Operation, OperationType
from corrcut.domain.time_point import TimePoint
from corrcut.preview.skip_playback import (
    skip_exit_seconds,
    skip_ranges_from_operations,
    skip_rewind_seconds,
)


def _skip(start: float, end: float, *, enabled: bool = True) -> Operation:
    op = Operation.create(
        OperationType.SKIP,
        -1,
        [],
        TimePoint(Fraction(str(start))),
        TimePoint(Fraction(str(end))),
    )
    op.enabled = enabled
    return op


def test_skip_exit_none_outside_range() -> None:
    ranges = [(10.0, 20.0)]
    assert skip_exit_seconds(9.999, ranges) is None
    assert skip_exit_seconds(20.0, ranges) is None
    assert skip_exit_seconds(25.0, ranges) is None


def test_skip_exit_inside_single_range() -> None:
    ranges = [(10.0, 20.0)]
    assert skip_exit_seconds(10.0, ranges) == 20.0
    assert skip_exit_seconds(15.0, ranges) == 20.0
    assert skip_exit_seconds(19.999, ranges) == 20.0


def test_skip_exit_chains_overlapping_and_abutting() -> None:
    ranges = [(1.0, 3.0), (2.5, 5.0), (5.0, 7.0), (10.0, 12.0)]
    assert skip_exit_seconds(1.5, ranges) == 7.0
    assert skip_exit_seconds(5.5, ranges) == 7.0
    assert skip_exit_seconds(10.5, ranges) == 12.0


def test_skip_exit_does_not_chain_across_gap() -> None:
    ranges = [(1.0, 2.0), (2.1, 3.0)]
    assert skip_exit_seconds(1.5, ranges) == 2.0


def test_skip_rewind_lands_just_before_the_block() -> None:
    ranges = [(10.0, 20.0)]
    assert skip_rewind_seconds(15.0, ranges) == 10.0 - 1e-3
    assert skip_rewind_seconds(10.0, ranges) == 10.0 - 1e-3
    assert skip_rewind_seconds(20.0, ranges) is None


def test_skip_rewind_chains_to_the_earliest_start() -> None:
    ranges = [(1.0, 3.0), (2.5, 5.0), (5.0, 7.0), (10.0, 12.0)]
    assert skip_rewind_seconds(6.0, ranges) == 1.0 - 1e-3
    assert skip_rewind_seconds(10.5, ranges) == 10.0 - 1e-3


def test_skip_rewind_at_timeline_start_stays_inside() -> None:
    assert skip_rewind_seconds(1.0, [(0.0, 4.0)]) == 0.0


def test_skip_ranges_from_operations_filters_and_sorts() -> None:
    ops = [
        _skip(5.0, 6.0),
        _skip(1.0, 2.0, enabled=False),
        Operation.create(
            OperationType.MUTE,
            1,
            [],
            TimePoint(Fraction(0)),
            TimePoint(Fraction(1)),
        ),
        _skip(3.0, 4.0),
    ]
    assert skip_ranges_from_operations(ops) == [(3.0, 4.0), (5.0, 6.0)]
