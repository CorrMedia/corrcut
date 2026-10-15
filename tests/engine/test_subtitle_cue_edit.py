"""Tests for subtitle cue add/merge/delete."""

from __future__ import annotations

from pathlib import Path

from corrcut.domain.project import Project, SourceFileRecord
from corrcut.domain.subtitle_cue import SubtitleCue, SubtitleSource
from corrcut.domain.time_point import TimePoint
from corrcut.engine.subtitles.cue_edit import (
    add_cue_at,
    can_merge_with_next,
    can_merge_with_previous,
    cue_can_reset_to_generated,
    delete_cue,
    merge_cues,
)
from corrcut.engine.subtitles.cue_sets import install_cue_set_for_tests


def _cue(start: float, end: float, text: str = "line") -> SubtitleCue:
    rate = 48_000
    return SubtitleCue.create(
        start=TimePoint.from_seconds_float(start, rate),
        end=TimePoint.from_seconds_float(end, rate),
        text=text,
        edited_text=text,
        source=SubtitleSource.EMBEDDED,
    )


def _project(*cues: SubtitleCue) -> Project:
    project = Project(
        source=SourceFileRecord(
            path=Path("movie.mkv"),
            fingerprint={},
            probe={"duration_seconds": 120.0},
        ),
    )
    install_cue_set_for_tests(project, list(cues), source=SubtitleSource.EMBEDDED)
    return project


def test_add_cue_at_playhead_in_gap() -> None:
    project = _project(_cue(1.0, 2.0), _cue(6.0, 7.0))
    new = add_cue_at(project, 3.0)
    assert float(new.start.seconds) == 3.0
    assert float(new.end.seconds) == 5.0
    assert new.source is SubtitleSource.USER
    assert len(project.subtitle_cues) == 3


def test_add_cue_respects_next_cue_start() -> None:
    project = _project(_cue(1.0, 2.0), _cue(4.0, 5.0))
    new = add_cue_at(project, 2.5, duration_seconds=5.0)
    assert float(new.end.seconds) == 4.0


def test_delete_cue() -> None:
    project = _project(_cue(1.0, 2.0, "a"), _cue(3.0, 4.0, "b"))
    cue_id = project.subtitle_cues[0].id
    assert delete_cue(project, cue_id)
    assert len(project.subtitle_cues) == 1
    assert project.subtitle_cues[0].text == "b"


def test_merge_adjacent_cues() -> None:
    left = _cue(1.0, 2.0, "Hello")
    right = _cue(2.0, 3.0, "world")
    project = _project(left, right)
    merged = merge_cues(project, left.id, right.id)
    assert merged is not None
    assert merged.text == "Hello world"
    assert merged.edited_text == "Hello world"
    assert float(merged.start.seconds) == 1.0
    assert float(merged.end.seconds) == 3.0
    assert len(project.subtitle_cues) == 1


def test_merge_rejects_non_adjacent() -> None:
    first = _cue(1.0, 2.0)
    middle = _cue(3.0, 4.0)
    second = _cue(5.0, 6.0)
    project = _project(first, middle, second)
    assert merge_cues(project, first.id, second.id) is None


def test_cue_can_reset_to_generated() -> None:
    embedded = _cue(1.0, 2.0)
    user = SubtitleCue.create(
        start=embedded.start,
        end=embedded.end,
        text="",
        source=SubtitleSource.USER,
        user_edited=True,
    )
    assert cue_can_reset_to_generated(embedded)
    assert not cue_can_reset_to_generated(user)


def test_merge_neighbor_checks() -> None:
    cues = [_cue(1.0, 2.0), _cue(2.0, 3.0), _cue(4.0, 5.0)]
    middle = cues[1]
    assert not can_merge_with_previous(cues, cues[0].id)
    assert can_merge_with_next(cues, cues[0].id)
    assert can_merge_with_previous(cues, middle.id)
    assert can_merge_with_next(cues, middle.id)
    assert can_merge_with_previous(cues, cues[2].id)
    assert not can_merge_with_next(cues, cues[2].id)
