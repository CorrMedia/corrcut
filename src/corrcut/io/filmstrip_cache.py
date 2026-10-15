"""Disk budget for filmstrip JPEG folders under ``cache/filmstrip/<fingerprint>``."""

from __future__ import annotations

import logging
import shutil
import threading
import time
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

# Filmstrip stills are cheap to rebuild; keep a soft cap and drop whole sources.
FILMSTRIP_CACHE_MAX_BYTES = 1_500_000_000
_LAST_USED_NAME = ".last_used"
_PART_SUFFIX = ".part.jpg"


@dataclass(frozen=True)
class FilmstripPruneResult:
    deleted_dirs: int
    deleted_bytes: int
    removed_parts: int
    total_bytes: int


def filmstrip_root(cache_root: Path) -> Path:
    return cache_root / "filmstrip"


def touch_filmstrip_cache(cache_dir: Path) -> None:
    """Record that this source's filmstrip folder is in active use."""
    try:
        cache_dir.mkdir(parents=True, exist_ok=True)
        stamp = cache_dir / _LAST_USED_NAME
        stamp.write_text(f"{time.time():.3f}\n", encoding="utf-8")
    except OSError:
        logger.debug("Could not touch filmstrip cache %s", cache_dir, exc_info=True)


def schedule_filmstrip_cache_prune(
    cache_root: Path,
    *,
    max_bytes: int = FILMSTRIP_CACHE_MAX_BYTES,
    protect_keys: set[str] | None = None,
) -> None:
    """Run ``prune_filmstrip_cache`` on a daemon thread."""

    def _run() -> None:
        try:
            result = prune_filmstrip_cache(
                cache_root,
                max_bytes=max_bytes,
                protect_keys=protect_keys,
            )
        except Exception:
            logger.warning("Filmstrip cache prune failed", exc_info=True)
            return
        if result.deleted_dirs or result.removed_parts:
            logger.info(
                "Filmstrip cache prune: removed %d source(s) (%d bytes), "
                "%d leftover part file(s); now %d bytes",
                result.deleted_dirs,
                result.deleted_bytes,
                result.removed_parts,
                result.total_bytes,
            )

    threading.Thread(target=_run, name="filmstrip-cache-prune", daemon=True).start()


def prune_filmstrip_cache(
    cache_root: Path,
    *,
    max_bytes: int = FILMSTRIP_CACHE_MAX_BYTES,
    protect_keys: set[str] | None = None,
) -> FilmstripPruneResult:
    """Delete oldest fingerprint folders until ``max_bytes`` is met.

    Also removes leftover ``*.part.jpg`` files. Protected keys (open project
    fingerprints) are never deleted.
    """
    root = filmstrip_root(cache_root)
    protect = set(protect_keys or ())
    removed_parts = _remove_part_files(root)
    if not root.is_dir():
        return FilmstripPruneResult(0, 0, removed_parts, 0)

    entries = _source_entries(root)
    total = sum(entry.bytes for entry in entries)
    if total <= max_bytes:
        return FilmstripPruneResult(0, 0, removed_parts, total)

    deleted_dirs = 0
    deleted_bytes = 0
    # Oldest first; skip anything still in use.
    for entry in sorted(entries, key=lambda item: (item.last_used, item.key)):
        if total <= max_bytes:
            break
        if entry.key in protect:
            continue
        try:
            shutil.rmtree(entry.path)
        except OSError:
            logger.debug("Could not remove filmstrip cache %s", entry.path, exc_info=True)
            continue
        deleted_dirs += 1
        deleted_bytes += entry.bytes
        total -= entry.bytes

    return FilmstripPruneResult(deleted_dirs, deleted_bytes, removed_parts, max(0, total))


@dataclass(frozen=True)
class _SourceEntry:
    key: str
    path: Path
    bytes: int
    last_used: float


def _source_entries(root: Path) -> list[_SourceEntry]:
    entries: list[_SourceEntry] = []
    try:
        children = list(root.iterdir())
    except OSError:
        return entries
    for child in children:
        if not child.is_dir():
            continue
        size = _dir_bytes(child)
        entries.append(
            _SourceEntry(
                key=child.name,
                path=child,
                bytes=size,
                last_used=_read_last_used(child),
            )
        )
    return entries


def _read_last_used(cache_dir: Path) -> float:
    stamp = cache_dir / _LAST_USED_NAME
    try:
        if stamp.is_file():
            text = stamp.read_text(encoding="utf-8").strip()
            if text:
                return float(text)
    except (OSError, ValueError):
        pass
    try:
        return stamp.stat().st_mtime if stamp.is_file() else cache_dir.stat().st_mtime
    except OSError:
        return 0.0


def _dir_bytes(path: Path) -> int:
    total = 0
    try:
        for child in path.rglob("*"):
            try:
                if child.is_file():
                    total += child.stat().st_size
            except OSError:
                continue
    except OSError:
        return total
    return total


def _remove_part_files(root: Path) -> int:
    if not root.is_dir():
        return 0
    removed = 0
    try:
        parts = list(root.rglob(f"*{_PART_SUFFIX}"))
    except OSError:
        return 0
    for part in parts:
        try:
            part.unlink(missing_ok=True)
            removed += 1
        except OSError:
            continue
    return removed
