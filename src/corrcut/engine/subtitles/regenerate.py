"""Regenerate edited_text from original cue text."""

from __future__ import annotations

from corrcut.domain.project import Project


def regenerate_edited_texts(project: Project) -> int:
    """Refresh edited_text from text for non-user-edited cues. Returns change count."""
    if not project.subtitle_cues:
        return 0

    changed = 0
    for cue in project.subtitle_cues:
        if cue.user_edited:
            continue
        if cue.edited_text != cue.text:
            cue.edited_text = cue.text
            changed += 1
    return changed
