"""Ingest embedded or Whisper subtitles into project subtitle_cues."""

from __future__ import annotations

import tempfile
import threading
from dataclasses import dataclass
from pathlib import Path

from corrcut.ai.subtitles import (
    extract_subtitle_srt,
    is_bitmap_subtitle_codec,
    is_ocr_subtitle_codec,
    is_text_subtitle_codec,
    parse_srt,
    select_subtitle_stream_index,
)
from corrcut.domain.project import Project
from corrcut.domain.subtitle_cue import SubtitleCue, SubtitleSource
from corrcut.domain.time_point import TimePoint
from corrcut.engine.subtitles.cue_sets import (
    ensure_active_source,
    promote_source_if_better,
    set_cues_for_source,
)
from corrcut.engine.subtitles.ocr_refine import refine_project_ocr_cues
from corrcut.engine.subtitles.regenerate import regenerate_edited_texts
from corrcut.engine.subtitles.words_to_cues import words_to_subtitle_cues
from corrcut.io.subtitle_cache import (
    load_embedded_subtitle_cache,
    save_embedded_subtitle_cache,
)


def _srt_cues_to_project(cues, *, source: SubtitleSource) -> list[SubtitleCue]:
    rows: list[SubtitleCue] = []
    for cue in cues:
        start = TimePoint.from_seconds_float(float(cue.start), 48_000)
        end = TimePoint.from_seconds_float(float(cue.end), 48_000)
        rows.append(
            SubtitleCue.create(
                start=start,
                end=end,
                text=cue.text,
                source=source,
            )
        )
    return rows


def has_text_subtitle_streams(probe: dict[str, object]) -> bool:
    streams = probe.get("subtitle_streams", [])
    return any(
        isinstance(s, dict) and is_text_subtitle_codec(str(s.get("codec", "")))
        for s in streams
    )


def has_bitmap_subtitle_streams(probe: dict[str, object], *, ocr_only: bool = True) -> bool:
    streams = probe.get("subtitle_streams", [])
    predicate = is_ocr_subtitle_codec if ocr_only else is_bitmap_subtitle_codec
    return any(isinstance(s, dict) and predicate(str(s.get("codec", ""))) for s in streams)


@dataclass(frozen=True)
class EmbeddedSubtitleExtract:
    """Cues read off the UI thread. Apply them only if *generation* is still current."""

    generation: int
    stream_index: int
    cues: tuple[SubtitleCue, ...]
    error: str | None = None


def embedded_text_stream_label(project: Project) -> str | None:
    """Language and codec of the text track that would be extracted."""
    if project.source is None:
        return None
    probe = project.source.probe or {}
    subtitle_streams = [
        s for s in probe.get("subtitle_streams", []) if isinstance(s, dict)
    ]
    track = project.selected_track()
    audio_language = track.language if track is not None else None
    stream_index = select_subtitle_stream_index(
        subtitle_streams,
        preferred_index=project.subtitle_settings.stream_index,
        audio_language=audio_language,
    )
    if stream_index is None:
        return None
    stream = next(
        (s for s in subtitle_streams if int(s.get("index", -1)) == stream_index),
        None,
    )
    if stream is None:
        return None
    codec = str(stream.get("codec", "")).strip()
    language = str(stream.get("language") or "").strip()
    label = f"#{stream_index} {codec}".strip()
    if language:
        label = f"{label} ({language})"
    return label


def read_embedded_subtitle_cues(
    project: Project,
    *,
    cache_dir: Path | None = None,
    cancel_event: threading.Event | None = None,
) -> tuple[int, list[SubtitleCue]] | None:
    """Read the selected text track without mutating *project*.

    Returns None when the file has no text subtitle track. An empty list means
    the track produced no cues. Raises on extract failure or cancellation.
    """
    if project.source is None:
        return None
    probe = project.source.probe or {}
    subtitle_streams = [
        s for s in probe.get("subtitle_streams", []) if isinstance(s, dict)
    ]
    text_streams = [
        s for s in subtitle_streams if is_text_subtitle_codec(str(s.get("codec", "")))
    ]
    if not text_streams:
        return None

    track = project.selected_track()
    audio_language = track.language if track is not None else None
    preferred_index = project.subtitle_settings.stream_index
    fingerprint = project.source.fingerprint
    source_path = project.source.path.resolve()
    stream_index = select_subtitle_stream_index(
        subtitle_streams,
        preferred_index=preferred_index,
        audio_language=audio_language,
    )
    if stream_index is None or not source_path.is_file():
        return None

    if cache_dir is not None:
        cached = load_embedded_subtitle_cache(cache_dir, fingerprint, stream_index)
        if cached:
            return stream_index, cached

    with tempfile.TemporaryDirectory(prefix="ms_sub_") as tmp:
        srt_path = Path(tmp) / "extracted.srt"
        extract_subtitle_srt(
            source_path,
            stream_index,
            srt_path,
            cancel_event=cancel_event,
        )
        content = srt_path.read_text(encoding="utf-8", errors="replace")
    parsed = parse_srt(content)
    rows = _srt_cues_to_project(parsed, source=SubtitleSource.EMBEDDED)
    if rows and cache_dir is not None:
        save_embedded_subtitle_cache(cache_dir, fingerprint, stream_index, rows)
    return stream_index, rows


def apply_embedded_subtitle_cues(
    project: Project,
    stream_index: int,
    cues: list[SubtitleCue],
) -> bool:
    """Store embedded cues on *project*. Call this on the UI thread."""
    if not cues:
        return False
    project.subtitle_settings.stream_index = stream_index
    set_cues_for_source(project, SubtitleSource.EMBEDDED, cues)
    promote_source_if_better(project, SubtitleSource.EMBEDDED)
    regenerate_edited_texts(project)
    return True


def ingest_embedded_subtitles(
    project: Project,
    *,
    cache_dir: Path | None = None,
    cancel_event: threading.Event | None = None,
) -> bool:
    """Extract embedded text subtitles into project.subtitle_cues."""
    found = read_embedded_subtitle_cues(
        project,
        cache_dir=cache_dir,
        cancel_event=cancel_event,
    )
    if found is None:
        return False
    stream_index, cues = found
    return apply_embedded_subtitle_cues(project, stream_index, cues)


def ingest_whisper_subtitles(project: Project) -> bool:
    """Build subtitle_cues from cached Whisper words."""
    cache = project.whisper_cache
    if cache is None or not cache.words:
        return False
    rows = words_to_subtitle_cues(cache.transcript_words())
    set_cues_for_source(project, SubtitleSource.WHISPER, rows)
    promote_source_if_better(project, SubtitleSource.WHISPER)
    regenerate_edited_texts(project)
    return True


def ingest_ocr_subtitles(project: Project, cues: list) -> bool:
    """Store OCR subtitle cues on the project."""
    if not cues:
        return False
    rows = _srt_cues_to_project(cues, source=SubtitleSource.OCR)
    set_cues_for_source(project, SubtitleSource.OCR, rows)
    refine_project_ocr_cues(project)
    promote_source_if_better(project, SubtitleSource.OCR)
    regenerate_edited_texts(project)
    return True


def refresh_project_subtitles(
    project: Project,
    *,
    cancel_event: threading.Event | None = None,
) -> str | None:
    """
    Populate subtitle_cues using source priority: embedded text, then Whisper cache.

    Returns a short status label: ``embedded``, ``whisper``, or ``None``.
    """
    if ingest_embedded_subtitles(project, cancel_event=cancel_event):
        return "embedded"
    if ingest_whisper_subtitles(project):
        return "whisper"
    ensure_active_source(project)
    return None
