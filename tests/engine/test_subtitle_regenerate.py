"""Regenerate edited subtitle lines."""

from __future__ import annotations

from fractions import Fraction

from corrcut.domain.project import Project
from corrcut.domain.subtitle_cue import SubtitleCue, SubtitleSource
from corrcut.domain.time_point import TimePoint
from corrcut.engine.subtitles.cue_sets import install_cue_set_for_tests
from corrcut.engine.subtitles.regenerate import regenerate_edited_texts


def _project_with_cue(
    text: str,
    *,
    edited_text: str | None = None,
    user_edited: bool = False,
) -> Project:
    cue = SubtitleCue.create(
        start=TimePoint(Fraction(1)),
        end=TimePoint(Fraction(3)),
        text=text,
        edited_text=edited_text if edited_text is not None else text,
        source=SubtitleSource.EMBEDDED,
        user_edited=user_edited,
    )
    project = Project(selected_audio_stream_index=0)
    install_cue_set_for_tests(project, [cue], source=SubtitleSource.EMBEDDED)
    return project


def test_regenerate_restores_original_text_for_non_user_edited_cues() -> None:
    project = _project_with_cue("What the fuck", edited_text="What the ***")
    changed = regenerate_edited_texts(project)
    assert changed == 1
    assert project.subtitle_cues[0].edited_text == "What the fuck"


def test_regenerate_skips_user_edited_cues() -> None:
    project = _project_with_cue(
        "What the fuck",
        edited_text="What the ***",
        user_edited=True,
    )
    changed = regenerate_edited_texts(project)
    assert changed == 0
    assert project.subtitle_cues[0].edited_text == "What the ***"
