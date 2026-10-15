"""Disk cache for ffprobe results keyed by source fingerprint (Phase 1)."""

from __future__ import annotations

import json
import logging
import threading
from pathlib import Path

from corrcut.engine.jobs.cancel import check_cancelled
from corrcut.engine.probe import (
    PROBE_CACHE_VERSION,
    MediaProbeResult,
    probe_media,
    probe_result_from_dict,
    probe_result_to_dict,
)
from corrcut.io.fingerprint import fingerprint_file

logger = logging.getLogger(__name__)


def fingerprint_cache_key(fingerprint: dict[str, object]) -> str:
    tail = fingerprint.get("tail_sha256") or ""
    return f"{fingerprint['size']}_{fingerprint['head_sha256']}_{tail}"


def _cache_path(cache_root: Path, fingerprint: dict[str, object]) -> Path:
    return cache_root / "probes" / f"{fingerprint_cache_key(fingerprint)}.json"


def load_probe_cache(cache_root: Path, fingerprint: dict[str, object]) -> MediaProbeResult | None:
    path = _cache_path(cache_root, fingerprint)
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if int(data.get("probe_cache_version", 1)) < PROBE_CACHE_VERSION:
            return None
        return probe_result_from_dict(data)
    except (json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
        logger.warning("Ignoring corrupt probe cache %s: %s", path, exc)
        return None


def save_probe_cache(
    cache_root: Path,
    fingerprint: dict[str, object],
    probe: MediaProbeResult,
) -> Path:
    path = _cache_path(cache_root, fingerprint)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(probe_result_to_dict(probe), indent=2), encoding="utf-8")
    return path


def probe_media_cached(
    path: Path,
    cache_root: Path,
    *,
    ffprobe: str = "ffprobe",
    fingerprint: dict[str, object] | None = None,
    cancel_event: threading.Event | None = None,
) -> tuple[MediaProbeResult, dict[str, object], bool]:
    """
    Return (probe, fingerprint, cache_hit).

    Skips ffprobe when a valid cache entry exists for the fingerprint.
    """
    check_cancelled(cancel_event)
    fp = fingerprint if fingerprint is not None else fingerprint_file(path)
    check_cancelled(cancel_event)
    cached = load_probe_cache(cache_root, fp)
    if cached is not None:
        if cached.path != path.resolve():
            cached = MediaProbeResult(
                path=path.resolve(),
                format_name=cached.format_name,
                duration_seconds=cached.duration_seconds,
                video_streams=cached.video_streams,
                audio_streams=cached.audio_streams,
                subtitle_streams=cached.subtitle_streams,
            )
        return cached, fp, True

    probe = probe_media(path, ffprobe=ffprobe, cancel_event=cancel_event)
    save_probe_cache(cache_root, fp, probe)
    return probe, fp, False
