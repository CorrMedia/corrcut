"""Disk cache for extracted text subtitle cues, keyed by source fingerprint."""

from __future__ import annotations

import json
import logging
from pathlib import Path

from corrcut.domain.subtitle_cue import SubtitleCue, SubtitleSource
from corrcut.domain.time_point import TimePoint
from corrcut.io.probe_cache import fingerprint_cache_key

logger = logging.getLogger(__name__)

SUBTITLE_CACHE_VERSION = 1


def _cache_path(cache_root: Path, fingerprint: dict[str, object], stream_index: int) -> Path:
    return (
        cache_root
        / "subtitles"
        / f"{fingerprint_cache_key(fingerprint)}_{stream_index}.json"
    )


def load_embedded_subtitle_cache(
    cache_root: Path,
    fingerprint: dict[str, object],
    stream_index: int,
) -> list[SubtitleCue] | None:
    """Return cached cues for *stream_index*, or None on a miss or bad entry."""
    try:
        path = _cache_path(cache_root, fingerprint, stream_index)
    except KeyError:
        return None
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if int(data.get("subtitle_cache_version", 0)) != SUBTITLE_CACHE_VERSION:
            return None
        cues: list[SubtitleCue] = []
        for row in data.get("cues", []):
            if not isinstance(row, dict):
                return None
            cues.append(
                SubtitleCue.create(
                    start=TimePoint.from_json(row["start"]),
                    end=TimePoint.from_json(row["end"]),
                    text=str(row.get("text", "")),
                    source=SubtitleSource.EMBEDDED,
                )
            )
        return cues
    except (json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
        logger.warning("Ignoring corrupt subtitle cache %s: %s", path, exc)
        return None


def save_embedded_subtitle_cache(
    cache_root: Path,
    fingerprint: dict[str, object],
    stream_index: int,
    cues: list[SubtitleCue],
) -> Path | None:
    """Write *cues* for *stream_index*. Empty lists are not cached."""
    if not cues:
        return None
    try:
        path = _cache_path(cache_root, fingerprint, stream_index)
    except KeyError:
        return None
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "subtitle_cache_version": SUBTITLE_CACHE_VERSION,
        "stream_index": stream_index,
        "cues": [
            {
                "start": cue.start.to_json(),
                "end": cue.end.to_json(),
                "text": cue.text,
            }
            for cue in cues
        ],
    }
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path
