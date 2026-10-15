"""Helpers for relinking a project to a moved or replacement media file."""

from __future__ import annotations

from pathlib import Path

from corrcut.domain.project import Project
from corrcut.engine.probe import MediaProbeResult


def fingerprint_matches(stored: dict[str, object], new: dict[str, object]) -> bool:
    """True when cheap file fingerprints match (same bytes at head/tail and size)."""
    for key in ("size", "head_sha256", "tail_sha256"):
        if stored.get(key) != new.get(key):
            return False
    return True


def missing_track_stream_indices(project: Project, probe: MediaProbeResult) -> list[int]:
    """Stream indices referenced by the project but absent from the replacement probe."""
    audio_indices = {a.index for a in probe.audio_streams}
    return [t.stream_index for t in project.tracks if t.stream_index not in audio_indices]


def apply_relink(
    project: Project,
    path: Path,
    probe: MediaProbeResult,
    fingerprint: dict[str, object],
) -> None:
    """Point the project at replacement media and refresh cached probe metadata."""
    if project.source is None:
        raise ValueError("project has no source to relink")
    project.source.path = path.resolve()
    project.source.fingerprint = fingerprint
    project.refresh_probe_metadata(probe)
