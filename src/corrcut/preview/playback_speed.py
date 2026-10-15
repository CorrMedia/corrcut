"""Preview shuttle speeds, in the style of Resolve's JKL keys.

L steps up the forward ladder (1×, 2×, 4×, …). J steps up the reverse
ladder. The opposite key steps back down, then crosses through 1× into
the other direction. K stops in place and returns the shuttle to 1×.
Holding K and tapping J or L uses the slow-motion ladder.
"""

from __future__ import annotations

import math

FAST_STEPS: tuple[float, ...] = (1.0, 2.0, 4.0, 8.0, 16.0, 32.0)
SLOW_STEPS: tuple[float, ...] = (0.5, 0.25, 0.125)

# Menu presets. Forward includes the slow-motion steps; reverse mirrors them.
FORWARD_RATES: tuple[float, ...] = SLOW_STEPS[::-1] + FAST_STEPS
REVERSE_RATES: tuple[float, ...] = tuple(-rate for rate in FORWARD_RATES)

_MIN_RATE = SLOW_STEPS[-1]
_MAX_RATE = FAST_STEPS[-1]


def clamp_playback_rate(rate: float) -> float:
    """Signed preview rate inside the shuttle range. ``0`` becomes ``1``."""
    value = float(rate)
    if not math.isfinite(value) or value == 0.0:
        return 1.0
    magnitude = min(_MAX_RATE, max(_MIN_RATE, abs(value)))
    return math.copysign(magnitude, value)


def shuttle_target(
    current: float,
    direction: int,
    *,
    playing: bool,
    slow: bool = False,
) -> float:
    """Rate after one J or L tap.

    A stopped transport starts at ±1×. While playing, taps step the fast
    ladder. ``slow`` is the K+J / K+L ladder.
    """
    if slow or playing:
        return step_shuttle(current, direction, slow=slow)
    return -1.0 if direction < 0 else 1.0


def step_shuttle(current: float, direction: int, *, slow: bool = False) -> float:
    """Next signed rate for one J (``direction < 0``) or L (``direction > 0``) tap."""
    sign = -1.0 if direction < 0 else 1.0
    steps = SLOW_STEPS if slow else FAST_STEPS
    signed_current = float(current)
    on_ladder = _ladder_index(abs(signed_current), steps)
    going_this_way = signed_current * sign > 0.0
    if going_this_way and on_ladder is not None:
        return sign * steps[min(on_ladder + 1, len(steps) - 1)]
    going_opposite = signed_current * sign < 0.0
    if going_opposite and on_ladder is not None:
        if on_ladder == 0:
            return sign * steps[0]
        return -sign * steps[on_ladder - 1]
    return sign * steps[0]


def format_playback_rate(rate: float) -> str:
    """``2×``, ``0.5×``, ``−2×``."""
    sign = "−" if float(rate) < 0.0 else ""
    magnitude = abs(float(rate))
    if magnitude >= 1.0 and abs(magnitude - round(magnitude)) < 1e-4:
        body = str(int(round(magnitude)))
    else:
        body = f"{magnitude:.3f}".rstrip("0").rstrip(".")
    return f"{sign}{body}×"


def _ladder_index(magnitude: float, steps: tuple[float, ...]) -> int | None:
    for index, step in enumerate(steps):
        if abs(magnitude - step) <= 1e-4 * max(1.0, step):
            return index
    return None
