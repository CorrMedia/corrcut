"""Multi-source subtitle cue storage and active-source selection."""

from __future__ import annotations

from typing import TYPE_CHECKING

from corrcut.domain.subtitle_cue import SubtitleCue, SubtitleSource

if TYPE_CHECKING:
    from corrcut.domain.project import Project

# Lower rank = higher quality / preferred default.
_SOURCE_RANK: dict[SubtitleSource, int] = {
    SubtitleSource.EMBEDDED: 0,
    SubtitleSource.OCR: 1,
    SubtitleSource.WHISPER: 2,
    SubtitleSource.USER: 3,
}

_STORABLE_SOURCES = (
    SubtitleSource.EMBEDDED,
    SubtitleSource.OCR,
    SubtitleSource.WHISPER,
)

SOURCE_LABELS: dict[SubtitleSource, str] = {
    SubtitleSource.EMBEDDED: "Embedded text",
    SubtitleSource.OCR: "Bitmap OCR",
    SubtitleSource.WHISPER: "Whisper",
    SubtitleSource.USER: "Manual",
}


def source_rank(source: SubtitleSource) -> int:
    return _SOURCE_RANK.get(source, 99)


def source_label(source: SubtitleSource) -> str:
    return SOURCE_LABELS.get(source, source.value)


def coerce_subtitle_source(value: object) -> SubtitleSource | None:
    """Normalize combo/item data back to :class:`SubtitleSource`."""
    if isinstance(value, SubtitleSource):
        return value
    if isinstance(value, str):
        try:
            return SubtitleSource(value)
        except ValueError:
            return None
    return None


def infer_set_source(cues: list[SubtitleCue]) -> SubtitleSource:
    for source in _STORABLE_SOURCES:
        if any(cue.source == source for cue in cues):
            return source
    if cues and cues[0].source == SubtitleSource.USER:
        return SubtitleSource.EMBEDDED
    return SubtitleSource.EMBEDDED


def cues_for_source(project: Project, source: SubtitleSource) -> list[SubtitleCue]:
    return list(project.subtitle_cue_sets.get(source.value, []))


def available_sources(project: Project) -> list[SubtitleSource]:
    return [source for source in _STORABLE_SOURCES if cues_for_source(project, source)]


def best_available_source(project: Project) -> SubtitleSource | None:
    sources = available_sources(project)
    if not sources:
        return None
    return min(sources, key=source_rank)


def active_cues(project: Project) -> list[SubtitleCue]:
    if project.active_subtitle_source is None:
        return []
    return cues_for_source(project, project.active_subtitle_source)


def set_cues_for_source(
    project: Project,
    source: SubtitleSource,
    cues: list[SubtitleCue],
) -> None:
    if source not in _STORABLE_SOURCES:
        raise ValueError(f"Unsupported subtitle cue set source: {source}")
    project.subtitle_cue_sets[source.value] = list(cues)


def set_active_cues(project: Project, cues: list[SubtitleCue]) -> None:
    if project.active_subtitle_source is None:
        project.active_subtitle_source = infer_set_source(cues) if cues else None
    if project.active_subtitle_source is None:
        return
    set_cues_for_source(project, project.active_subtitle_source, cues)


def switch_active_source(project: Project, source: SubtitleSource) -> bool:
    if source not in _STORABLE_SOURCES:
        return False
    if not cues_for_source(project, source):
        return False
    project.active_subtitle_source = source
    return True


def ensure_active_source(project: Project) -> SubtitleSource | None:
    if (
        project.active_subtitle_source is not None
        and cues_for_source(project, project.active_subtitle_source)
    ):
        return project.active_subtitle_source
    best = best_available_source(project)
    project.active_subtitle_source = best
    return best


def promote_source_if_better(project: Project, source: SubtitleSource) -> None:
    """Switch the active set when a higher-quality source becomes available."""
    if source not in _STORABLE_SOURCES:
        return
    if not cues_for_source(project, source):
        return
    current = project.active_subtitle_source
    if current is None or not cues_for_source(project, current):
        project.active_subtitle_source = source
        return
    if source_rank(source) < source_rank(current):
        project.active_subtitle_source = source


def migrate_legacy_subtitle_cues(project: Project, cues: list[SubtitleCue]) -> None:
    if not cues or project.subtitle_cue_sets:
        return
    source = infer_set_source(cues)
    set_cues_for_source(project, source, cues)
    project.active_subtitle_source = source


def install_cue_set_for_tests(
    project: Project,
    cues: list[SubtitleCue],
    *,
    source: SubtitleSource | None = None,
) -> None:
    """Populate cue sets in unit tests."""
    resolved = source or infer_set_source(cues)
    set_cues_for_source(project, resolved, cues)
    project.active_subtitle_source = resolved
