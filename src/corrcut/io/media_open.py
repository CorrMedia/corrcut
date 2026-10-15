"""Prepare a project from a media file off the UI thread."""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from corrcut.domain.corr_sidecar import (
    load_corr_file,
    operations_from_corr_document,
    sidecar_path,
)
from corrcut.domain.project import Project
from corrcut.engine.jobs.cancel import JobCancelled, check_cancelled
from corrcut.io.probe_cache import probe_media_cached

logger = logging.getLogger(__name__)

Progress = Callable[[int, str], None]


@dataclass
class PreparedMediaOpen:
    """Probe and sidecar edits ready to attach to the editor.

    Text subtitle extraction is intentionally not part of this step. The editor
    starts that work in the background after the project is on screen.
    """

    project: Project
    probe_cache_hit: bool
    corr_path: Path
    sidecar_loaded: bool
    sidecar_error: str | None


def prepare_media_open(
    path: Path,
    cache_dir: Path,
    *,
    cancel_event: threading.Event | None = None,
    progress: Progress | None = None,
) -> PreparedMediaOpen:
    """Probe *path* and load a sibling corr.json.

    Safe to call from a worker thread. Does not touch Qt.
    """
    _report(progress, 8, "Probing media…")
    check_cancelled(cancel_event)
    probe, fingerprint, cache_hit = probe_media_cached(
        path,
        cache_dir,
        cancel_event=cancel_event,
    )
    project = Project.from_probe(probe, fingerprint)

    corr = sidecar_path(path)
    sidecar_loaded = False
    sidecar_error: str | None = None
    _report(progress, 40, "Reading sidecar…")
    check_cancelled(cancel_event)
    if corr.is_file():
        try:
            data = load_corr_file(corr)
            stream = project.selected_audio_stream_index or 0
            project.operations = operations_from_corr_document(
                data,
                default_stream_index=stream,
                tracks=project.tracks,
            )
            project.select_track_for_audio_edits()
            sidecar_loaded = True
        except JobCancelled:
            raise
        except Exception as exc:
            sidecar_error = str(exc)
            logger.warning("Could not auto-load corr sidecar %s: %s", corr, exc)

    _report(progress, 100, "Ready")
    return PreparedMediaOpen(
        project=project,
        probe_cache_hit=cache_hit,
        corr_path=corr,
        sidecar_loaded=sidecar_loaded,
        sidecar_error=sidecar_error,
    )


def _report(progress: Progress | None, percent: int, message: str) -> None:
    if progress is not None:
        progress(percent, message)
