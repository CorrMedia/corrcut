"""Build SRT files from project subtitle cues for export."""

from __future__ import annotations

from pathlib import Path

from corrcut.domain.project import Project
from corrcut.engine.subtitles.service import ensure_project_subtitles
from corrcut.engine.subtitles.srt import TextField, write_srt


def render_project_subtitle_srt(
    project: Project,
    output_srt: Path,
    *,
    field: TextField = "edited_text",
) -> int:
    """Write project subtitle cues to SRT. Returns cue count."""
    ensure_project_subtitles(project)
    if not project.subtitle_cues:
        raise ValueError("project has no subtitle cues")
    write_srt(project.subtitle_cues, output_srt, field=field)
    return len(project.subtitle_cues)
