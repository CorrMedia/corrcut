"""Add, merge, and delete project subtitle cues."""

from __future__ import annotations

from fractions import Fraction

from corrcut.domain.project import Project
from corrcut.domain.subtitle_cue import SubtitleCue, SubtitleSource
from corrcut.domain.time_point import TimePoint

DEFAULT_CUE_DURATION_SECONDS = 2.0
DEFAULT_SAMPLE_RATE = 48_000
_MIN_CUE_DURATION_SECONDS = 0.5


def cue_can_reset_to_generated(cue: SubtitleCue) -> bool:
    """True when a cue has an auto-generated edited line to restore."""
    return cue.source is not SubtitleSource.USER


def sort_subtitle_cues(cues: list[SubtitleCue]) -> list[SubtitleCue]:
    return sorted(cues, key=lambda cue: (cue.start.seconds, cue.end.seconds))


def project_sample_rate(project: Project) -> int:
    track = project.selected_track()
    if track is not None and track.sample_rate:
        return int(track.sample_rate)
    return DEFAULT_SAMPLE_RATE


def media_duration_seconds(project: Project) -> float | None:
    if project.source is None:
        return None
    probe = project.source.probe or {}
    duration = probe.get("duration_seconds")
    if duration is None:
        return None
    try:
        value = float(duration)
    except (TypeError, ValueError):
        return None
    return value if value > 0 else None


def _join_lines(left: str, right: str) -> str:
    left = left.strip()
    right = right.strip()
    if not left:
        return right
    if not right:
        return left
    return f"{left} {right}"


def add_cue_at(
    project: Project,
    seconds: float,
    *,
    duration_seconds: float = DEFAULT_CUE_DURATION_SECONDS,
) -> SubtitleCue:
    """Insert a new user cue at ``seconds`` with a sensible end time."""
    rate = project_sample_rate(project)
    start_s = max(0.0, float(seconds))
    start = TimePoint.from_seconds_float(start_s, rate)
    cues = sort_subtitle_cues(project.subtitle_cues)

    end_s = start_s + max(duration_seconds, _MIN_CUE_DURATION_SECONDS)
    media_end = media_duration_seconds(project)
    if media_end is not None:
        end_s = min(end_s, media_end)

    for cue in cues:
        if float(cue.start.seconds) > start_s:
            end_s = min(end_s, float(cue.start.seconds))
            break

    if end_s <= start_s:
        end_s = start_s + _MIN_CUE_DURATION_SECONDS
        if media_end is not None:
            end_s = min(end_s, media_end)
    if end_s <= start_s:
        end_s = start_s + Fraction(1, 10)

    end = TimePoint.from_seconds_float(end_s, rate)
    new_cue = SubtitleCue.create(
        start=start,
        end=end,
        text="",
        edited_text="",
        source=SubtitleSource.USER,
        user_edited=True,
    )
    project.subtitle_cues = sort_subtitle_cues(cues + [new_cue])
    return new_cue


def delete_cue(project: Project, cue_id: str) -> bool:
    """Remove a cue by id. Returns True when a cue was removed."""
    remaining = [cue for cue in project.subtitle_cues if cue.id != cue_id]
    if len(remaining) == len(project.subtitle_cues):
        return False
    project.subtitle_cues = sort_subtitle_cues(remaining)
    return True


def _adjacent_pair(
    cues: list[SubtitleCue],
    first_id: str,
    second_id: str,
) -> tuple[SubtitleCue, SubtitleCue] | None:
    ordered = sort_subtitle_cues(cues)
    ids = [cue.id for cue in ordered]
    if first_id not in ids or second_id not in ids:
        return None
    first_index = ids.index(first_id)
    second_index = ids.index(second_id)
    if abs(first_index - second_index) != 1:
        return None
    left_index = min(first_index, second_index)
    return ordered[left_index], ordered[left_index + 1]


def merge_cues(project: Project, first_id: str, second_id: str) -> SubtitleCue | None:
    """Merge two adjacent cues into one user cue."""
    pair = _adjacent_pair(project.subtitle_cues, first_id, second_id)
    if pair is None:
        return None
    left, right = pair
    merged = SubtitleCue.create(
        start=left.start,
        end=right.end,
        text=_join_lines(left.text, right.text),
        edited_text=_join_lines(left.edited_text, right.edited_text),
        source=SubtitleSource.USER,
        user_edited=left.user_edited or right.user_edited,
    )
    remaining = [
        cue
        for cue in project.subtitle_cues
        if cue.id not in {left.id, right.id}
    ]
    project.subtitle_cues = sort_subtitle_cues(remaining + [merged])
    return merged


def can_merge_with_previous(cues: list[SubtitleCue], cue_id: str) -> bool:
    ordered = sort_subtitle_cues(cues)
    ids = [cue.id for cue in ordered]
    if cue_id not in ids:
        return False
    return ids.index(cue_id) > 0


def can_merge_with_next(cues: list[SubtitleCue], cue_id: str) -> bool:
    ordered = sort_subtitle_cues(cues)
    ids = [cue.id for cue in ordered]
    if cue_id not in ids:
        return False
    return ids.index(cue_id) < len(ids) - 1
