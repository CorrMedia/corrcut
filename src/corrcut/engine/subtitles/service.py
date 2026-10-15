"""High-level subtitle workflow helpers."""

from __future__ import annotations

from corrcut.domain.project import Project
from corrcut.domain.whisper_cache import WhisperCache
from corrcut.engine.subtitles.ingest import refresh_project_subtitles
from corrcut.engine.subtitles.regenerate import regenerate_edited_texts


def store_whisper_cache(
    project: Project,
    *,
    stream_index: int,
    model: str,
    words,
) -> None:
    project.whisper_cache = WhisperCache.from_transcript_words(
        stream_index=stream_index,
        model=model,
        words=list(words),
    )


def ensure_project_subtitles(project: Project) -> None:
    """Ensure subtitle_cues exist; ingest from source when empty."""
    if project.subtitle_cues:
        regenerate_edited_texts(project)
        return
    refresh_project_subtitles(project)


def on_operations_changed(project: Project) -> int:
    """Call after operation edits; subtitles are no longer auto-censored."""
    return 0
