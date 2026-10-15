"""Subtitle display timing helpers."""

from __future__ import annotations

import bisect
from fractions import Fraction

from corrcut.domain.subtitle_cue import SubtitleCue

# Keep subtitles on screen briefly after their timed end so they do not vanish
# instantly, but clear before the next cue unless that cue is already active.
DEFAULT_POST_DISPLAY_SECONDS = 1.5
DEFAULT_MIN_DISPLAY_SECONDS = 0.5


def _cue_start(cue: SubtitleCue) -> Fraction:
    return cue.start.seconds


def _visible_until(
    cue: SubtitleCue,
    *,
    post_display_seconds: float = DEFAULT_POST_DISPLAY_SECONDS,
    min_display_seconds: float = DEFAULT_MIN_DISPLAY_SECONDS,
) -> Fraction:
    start = cue.start.seconds
    end = cue.end.seconds
    effective_end = max(end, start + Fraction(min_display_seconds))
    return effective_end + Fraction(post_display_seconds)


def _overlaps_previous(cues: list[SubtitleCue], index: int) -> bool:
    if index <= 0:
        return False
    return cues[index - 1].end.seconds > cues[index].start.seconds


def _rightmost_started(cues: list[SubtitleCue], t: Fraction) -> int:
    """Index of the last cue with ``start <= t``, or -1 when playback is earlier."""
    return bisect.bisect_right(cues, t, key=_cue_start) - 1


def _select_cue_index(
    cues: list[SubtitleCue],
    t: Fraction,
    index: int,
    *,
    post_display_seconds: float,
    min_display_seconds: float,
) -> int | None:
    """Cue visible at ``t`` among cues that have already started.

    ``index`` is the rightmost cue with ``start <= t``. Cues are ordered by
    start time. Non-overlapping cues resolve in one step; an overlap walks
    backward only through the overlapping run.
    """
    active: int | None = None
    i = index
    while i >= 0:
        cue = cues[i]
        if cue.start.seconds <= t < cue.end.seconds:
            active = i
        if not _overlaps_previous(cues, i):
            break
        i -= 1
    if active is not None:
        return active

    i = index
    while i >= 0:
        cue = cues[i]
        until = _visible_until(
            cue,
            post_display_seconds=post_display_seconds,
            min_display_seconds=min_display_seconds,
        )
        if cue.end.seconds <= t < until:
            return i
        if not _overlaps_previous(cues, i):
            break
        i -= 1
    return None


def _cue_list_signature(cues: list[SubtitleCue]) -> tuple[int, str | None, str | None]:
    if not cues:
        return (0, None, None)
    return (len(cues), cues[0].id, cues[-1].id)


def _resolve_subtitle_cue(
    cues: list[SubtitleCue],
    seconds: float,
    *,
    post_display_seconds: float = DEFAULT_POST_DISPLAY_SECONDS,
    min_display_seconds: float = DEFAULT_MIN_DISPLAY_SECONDS,
) -> tuple[SubtitleCue | None, float, float]:
    """Return the visible cue and the half-open span where that answer stays valid.

    The span is empty (``start == end``) when overlapping cues make a wider
    window unsafe. Callers should keep the returned cue until playback leaves
    the span.
    """
    if not cues:
        return None, float("-inf"), float("inf")

    t = Fraction(seconds).limit_denominator(48_000)
    index = _rightmost_started(cues, t)
    if index < 0:
        return None, float("-inf"), float(cues[0].start.seconds)

    selected = _select_cue_index(
        cues,
        t,
        index,
        post_display_seconds=post_display_seconds,
        min_display_seconds=min_display_seconds,
    )
    if selected is None:
        if _overlaps_previous(cues, index) or (
            index + 1 < len(cues) and cues[index].end.seconds > cues[index + 1].start.seconds
        ):
            return None, seconds, seconds
        gap_start = float(
            _visible_until(
                cues[index],
                post_display_seconds=post_display_seconds,
                min_display_seconds=min_display_seconds,
            )
        )
        next_start = cues[index + 1].start.seconds if index + 1 < len(cues) else None
        gap_end = float(next_start) if next_start is not None else float("inf")
        if gap_start <= seconds < gap_end:
            return None, gap_start, gap_end
        return None, seconds, seconds

    cue = cues[selected]
    overlaps = _overlaps_previous(cues, selected) or (
        selected + 1 < len(cues) and cue.end.seconds > cues[selected + 1].start.seconds
    )
    if overlaps:
        return cue, seconds, seconds

    start = float(cue.start.seconds)
    until = float(
        _visible_until(
            cue,
            post_display_seconds=post_display_seconds,
            min_display_seconds=min_display_seconds,
        )
    )
    if selected + 1 < len(cues):
        until = min(until, float(cues[selected + 1].start.seconds))
    if start <= seconds < until:
        return cue, start, until
    return cue, seconds, seconds


def cue_at_seconds(
    cues: list[SubtitleCue],
    seconds: float,
    *,
    post_display_seconds: float = DEFAULT_POST_DISPLAY_SECONDS,
    min_display_seconds: float = DEFAULT_MIN_DISPLAY_SECONDS,
) -> SubtitleCue | None:
    """
    Return the subtitle cue visible at ``seconds``.

    Cues are ordered by start time. The lookup bisects to the last cue that
    has started, then checks that cue (and an overlapping run, when there is
    one). Cues are shown during their [start, end) window, linger briefly
    afterward, and then clear even if the next cue has not started yet.
    """
    cue, _start, _end = _resolve_subtitle_cue(
        cues,
        seconds,
        post_display_seconds=post_display_seconds,
        min_display_seconds=min_display_seconds,
    )
    return cue


class SubtitleCueCursor:
    """Remember the visible cue until playback leaves its display window."""

    def __init__(self) -> None:
        self._ready = False
        self._start = 0.0
        self._end = 0.0
        self._cue: SubtitleCue | None = None
        self._signature: tuple[int, str | None, str | None] = (0, None, None)

    def reset(self) -> None:
        self._ready = False
        self._cue = None

    @property
    def current(self) -> SubtitleCue | None:
        return self._cue if self._ready else None

    def holds(self, cues: list[SubtitleCue], seconds: float) -> bool:
        """True when ``seconds`` is still inside the cached cue's window."""
        return (
            self._ready
            and self._signature == _cue_list_signature(cues)
            and self._start <= seconds < self._end
        )

    def cue_at(
        self,
        cues: list[SubtitleCue],
        seconds: float,
        *,
        post_display_seconds: float = DEFAULT_POST_DISPLAY_SECONDS,
        min_display_seconds: float = DEFAULT_MIN_DISPLAY_SECONDS,
    ) -> SubtitleCue | None:
        if self.holds(cues, seconds):
            return self._cue
        cue, start, end = _resolve_subtitle_cue(
            cues,
            seconds,
            post_display_seconds=post_display_seconds,
            min_display_seconds=min_display_seconds,
        )
        self._ready = True
        self._cue = cue
        self._start = start
        self._end = end
        self._signature = _cue_list_signature(cues)
        return cue
