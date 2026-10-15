"""One cached poster still per recent project.

The welcome screen only reads these files. Capture runs when a project is
opened or its media is relinked, off the UI thread.
"""

from __future__ import annotations

import hashlib
import logging
import threading
from collections.abc import Callable
from pathlib import Path

from corrcut.domain.project import Project
from corrcut.engine.filmstrip import extract_frame

logger = logging.getLogger(__name__)

POSTER_WIDTH = 112
POSTER_HEIGHT = 63
POSTER_SEEK_FRACTION = 0.10

_capture_lock = threading.Lock()


def poster_cache_path(cache_root: Path, project_path: Path) -> Path:
    digest = hashlib.sha256(str(project_path.resolve()).encode("utf-8")).hexdigest()[:16]
    return cache_root / "recent_posters" / f"{digest}-10.jpg"


def discard_recent_poster(cache_root: Path, project_path: Path) -> None:
    poster_cache_path(cache_root, project_path).unlink(missing_ok=True)


def poster_seek_seconds(duration: float | None) -> float | None:
    """A point past logos and fades: one tenth of the way through."""
    if duration is None or duration <= 0:
        return None
    return duration * POSTER_SEEK_FRACTION


def capture_recent_poster(media_path: Path, dest: Path, duration: float | None) -> bool:
    """Write a cover-cropped JPEG from about 10% into the movie."""
    seek = poster_seek_seconds(duration)
    if seek is None or duration is None:
        return False
    with _capture_lock:
        if dest.is_file():
            return True
        try:
            extract_frame(
                media_path,
                dest,
                seek,
                width=POSTER_WIDTH,
                height=POSTER_HEIGHT,
                duration=duration,
            )
        except Exception:
            logger.debug("Recent poster skipped for %s", media_path, exc_info=True)
            return False
        return dest.is_file()


def schedule_recent_poster(
    cache_root: Path,
    project: Project,
    *,
    start: Callable[[Callable[[], None]], None] | None = None,
) -> None:
    """Capture a poster when this saved project has media and no still yet."""
    request = _poster_request(cache_root, project)
    if request is None:
        return
    media_path, dest, duration = request

    def _run() -> None:
        capture_recent_poster(media_path, dest, duration)

    if start is None:
        threading.Thread(target=_run, name="recent-poster", daemon=True).start()
        return
    start(_run)


def _poster_request(
    cache_root: Path,
    project: Project,
) -> tuple[Path, Path, float | None] | None:
    project_path = project.project_path
    source = project.source
    if project_path is None or source is None or not source.path.is_file():
        return None
    dest = poster_cache_path(cache_root, project_path)
    if dest.is_file():
        return None
    return source.path, dest, _duration_seconds(source.probe)


def _duration_seconds(probe: dict[str, object]) -> float | None:
    raw = probe.get("duration_seconds")
    if isinstance(raw, bool) or not isinstance(raw, (int, float, str)):
        return None
    try:
        duration = float(raw)
    except ValueError:
        return None
    if duration <= 0:
        return None
    return duration
