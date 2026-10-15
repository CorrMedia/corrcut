"""Shuttle ladder for preview playback speed."""

from corrcut.preview.playback_speed import (
    clamp_playback_rate,
    format_playback_rate,
    shuttle_target,
    step_shuttle,
)


def test_forward_doubles_until_cap() -> None:
    assert step_shuttle(1, 1) == 2
    assert step_shuttle(2, 1) == 4
    assert step_shuttle(16, 1) == 32
    assert step_shuttle(32, 1) == 32


def test_opposite_key_steps_down_then_reverses() -> None:
    assert step_shuttle(4, -1) == 2
    assert step_shuttle(2, -1) == 1
    assert step_shuttle(1, -1) == -1
    assert step_shuttle(-1, -1) == -2
    assert step_shuttle(-4, 1) == -2
    assert step_shuttle(-1, 1) == 1


def test_slow_motion_halves_in_the_pressed_direction() -> None:
    assert step_shuttle(1, 1, slow=True) == 0.5
    assert step_shuttle(0.5, 1, slow=True) == 0.25
    assert step_shuttle(0.25, 1, slow=True) == 0.125
    assert step_shuttle(0.125, 1, slow=True) == 0.125
    assert step_shuttle(0.125, -1, slow=True) == 0.25
    assert step_shuttle(0.5, -1, slow=True) == -0.5
    assert step_shuttle(-0.5, -1, slow=True) == -0.25


def test_fast_key_leaves_slow_motion_at_1x() -> None:
    assert step_shuttle(0.25, 1) == 1
    assert step_shuttle(0.25, -1) == -1
    assert step_shuttle(-0.125, 1) == 1


def test_stopped_transport_starts_at_1x() -> None:
    assert shuttle_target(4, 1, playing=False) == 1
    assert shuttle_target(4, -1, playing=False) == -1
    assert shuttle_target(1, 1, playing=False) == 1
    assert shuttle_target(1, 1, playing=True) == 2
    assert shuttle_target(1, 1, playing=False, slow=True) == 0.5
    assert shuttle_target(0.5, 1, playing=True, slow=True) == 0.25


def test_format_and_clamp() -> None:
    assert format_playback_rate(1) == "1×"
    assert format_playback_rate(0.5) == "0.5×"
    assert format_playback_rate(0.125) == "0.125×"
    assert format_playback_rate(-2) == "−2×"
    assert clamp_playback_rate(0) == 1
    assert clamp_playback_rate(100) == 32
    assert clamp_playback_rate(-0.01) == -0.125
