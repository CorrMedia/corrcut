"""Extract text subtitles and convert cues to transcript words for lexicon matching."""

from __future__ import annotations

import logging
import re
import shutil
import subprocess
import tempfile
import threading
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path
from typing import Any

from corrcut.ai.language import whisper_language_code
from corrcut.ai.transcript import TranscriptWord
from corrcut.domain.time_point import TimePoint
from corrcut.engine.hidden_process import no_window_kwargs
from corrcut.engine.jobs.run_process import run_process
from corrcut.engine.toolchain import resolve_executable

logger = logging.getLogger(__name__)

_TEXT_SUBTITLE_CODECS = frozenset(
    {
        "subrip",
        "srt",
        "ass",
        "ssa",
        "mov_text",
        "webvtt",
        "text",
    }
)

_BITMAP_SUBTITLE_CODECS = frozenset(
    {
        "hdmv_pgs_subtitle",
        "pgssub",
        "dvd_subtitle",
        "dvdsub",
        "dvb_subtitle",
        "xsub",
    }
)

_OCR_SUBTITLE_CODECS = frozenset(
    {
        "hdmv_pgs_subtitle",
        "pgssub",
        "dvd_subtitle",
        "dvdsub",
    }
)

_TIMESTAMP_RE = re.compile(
    r"(\d{2}):(\d{2}):(\d{2})[,.](\d{3})\s*-->\s*(\d{2}):(\d{2}):(\d{2})[,.](\d{3})"
)
_TAG_RE = re.compile(r"<[^>]+>")
_TOKEN_RE = re.compile(r"[^\W_]+", re.UNICODE)


@dataclass(frozen=True)
class SubtitleCue:
    start: Fraction
    end: Fraction
    text: str


def _timestamp_to_fraction(h: str, m: str, s: str, ms: str) -> Fraction:
    return Fraction(int(h) * 3600 + int(m) * 60 + int(s), 1) + Fraction(int(ms), 1000)


def parse_srt(content: str) -> list[SubtitleCue]:
    """Parse SubRip text (also typical ffmpeg ``-c:s srt`` output)."""
    cues: list[SubtitleCue] = []
    blocks = re.split(r"\n\s*\n", content.strip())
    for block in blocks:
        lines = [ln.strip() for ln in block.splitlines() if ln.strip()]
        if len(lines) < 2:
            continue
        time_line = lines[1] if lines[0].isdigit() else lines[0]
        text_lines = lines[2:] if lines[0].isdigit() else lines[1:]
        match = _TIMESTAMP_RE.search(time_line)
        if match is None:
            continue
        g = match.groups()
        start = _timestamp_to_fraction(g[0], g[1], g[2], g[3])
        end = _timestamp_to_fraction(g[4], g[5], g[6], g[7])
        text = _TAG_RE.sub("", " ".join(text_lines)).strip()
        if not text:
            continue
        cues.append(SubtitleCue(start=start, end=end, text=text))
    return cues


def tokenize_cue_text(text: str) -> list[str]:
    clean = _TAG_RE.sub("", text.replace("\n", " "))
    return _TOKEN_RE.findall(clean)


def words_from_cues(
    cues: list[SubtitleCue],
    *,
    sample_rate: int,
) -> list[TranscriptWord]:
    """Split each cue into tokens with linearly interpolated timestamps."""
    words: list[TranscriptWord] = []
    for cue in cues:
        tokens = tokenize_cue_text(cue.text)
        if not tokens:
            continue
        duration = cue.end - cue.start
        if duration <= 0:
            duration = Fraction(1, 10)
        if len(tokens) == 1:
            spans = [(cue.start, cue.end)]
        else:
            step = duration / len(tokens)
            spans = [
                (cue.start + step * i, cue.start + step * (i + 1))
                for i in range(len(tokens))
            ]
        for token, (start_s, end_s) in zip(tokens, spans, strict=True):
            words.append(
                TranscriptWord(
                    text=token,
                    start=TimePoint.from_seconds_float(float(start_s), sample_rate),
                    end=TimePoint.from_seconds_float(float(end_s), sample_rate),
                    probability=0.92,
                    segment_text=cue.text,
                )
            )
    return words


def is_text_subtitle_codec(codec: str) -> bool:
    return codec.strip().lower() in _TEXT_SUBTITLE_CODECS


def is_bitmap_subtitle_codec(codec: str) -> bool:
    return codec.strip().lower() in _BITMAP_SUBTITLE_CODECS


def is_pgs_subtitle_codec(codec: str) -> bool:
    return codec.strip().lower() in {"hdmv_pgs_subtitle", "pgssub"}


def is_dvd_subtitle_codec(codec: str) -> bool:
    return codec.strip().lower() in {"dvd_subtitle", "dvdsub"}


def is_ocr_subtitle_codec(codec: str) -> bool:
    return codec.strip().lower() in _OCR_SUBTITLE_CODECS


def _language_key(raw: str | None) -> str | None:
    if raw is None:
        return None
    return whisper_language_code(raw) or raw.strip().lower()[:2]


def select_subtitle_stream_index(
    subtitle_streams: list[dict[str, Any]],
    *,
    preferred_index: int | None,
    audio_language: str | None,
) -> int | None:
    """Pick a text subtitle stream index, or None if none are usable."""
    text_streams = [
        s for s in subtitle_streams if is_text_subtitle_codec(str(s.get("codec", "")))
    ]
    if not text_streams:
        return None

    if preferred_index is not None:
        for s in text_streams:
            if int(s["index"]) == preferred_index:
                return preferred_index

    target_lang = _language_key(audio_language)
    if target_lang is not None:
        for s in text_streams:
            stream_lang = _language_key(str(s["language"]) if s.get("language") else None)
            if stream_lang == target_lang:
                return int(s["index"])

    return int(text_streams[0]["index"])


def _bitmap_stream_quality_rank(codec: str) -> int:
    """Lower is better: PGS before DVD before other OCR-capable bitmap codecs."""
    key = codec.strip().lower()
    if is_pgs_subtitle_codec(key):
        return 0
    if is_dvd_subtitle_codec(key):
        return 1
    return 2


def _best_bitmap_stream_index(streams: list[dict[str, Any]]) -> int:
    best = min(
        streams,
        key=lambda s: (
            _bitmap_stream_quality_rank(str(s.get("codec", ""))),
            int(s.get("index", 0)),
        ),
    )
    return int(best["index"])


def select_bitmap_subtitle_stream_index(
    subtitle_streams: list[dict[str, Any]],
    *,
    preferred_index: int | None,
    audio_language: str | None,
    ocr_only: bool = True,
) -> int | None:
    """Pick a bitmap subtitle stream index suitable for OCR, or None."""
    predicate = is_ocr_subtitle_codec if ocr_only else is_bitmap_subtitle_codec
    bitmap_streams = [
        s for s in subtitle_streams if predicate(str(s.get("codec", "")))
    ]
    if not bitmap_streams:
        return None

    if preferred_index is not None:
        for s in bitmap_streams:
            if int(s["index"]) == preferred_index:
                return preferred_index

    target_lang = _language_key(audio_language)
    if target_lang is not None:
        matching = [
            s
            for s in bitmap_streams
            if _language_key(str(s["language"]) if s.get("language") else None) == target_lang
        ]
        if matching:
            return _best_bitmap_stream_index(matching)

    return _best_bitmap_stream_index(bitmap_streams)


def extract_subtitle_srt(
    source_path: Path,
    stream_index: int,
    output_srt: Path,
    *,
    ffmpeg: str | None = None,
    timeout: int = 600,
    cancel_event: threading.Event | None = None,
) -> None:
    exe = resolve_executable("ffmpeg") if ffmpeg is None else ffmpeg
    cmd = [
        exe,
        "-hide_banner",
        "-y",
        "-i",
        str(source_path.resolve()),
        "-map",
        f"0:{stream_index}",
        "-c:s",
        "srt",
        "-f",
        "srt",
        str(output_srt.resolve()),
    ]
    result = run_process(cmd, cancel_event=cancel_event, timeout_s=timeout)
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "ffmpeg subtitle extract failed").strip()
        raise RuntimeError(detail)
    if not output_srt.is_file():
        raise RuntimeError("ffmpeg produced no subtitle file")


def extract_subtitle_sup(
    source_path: Path,
    stream_index: int,
    output_sup: Path,
    *,
    ffmpeg: str | None = None,
    timeout: int = 600,
) -> None:
    """Extract a PGS subtitle stream to a standalone .sup file."""
    exe = resolve_executable("ffmpeg") if ffmpeg is None else ffmpeg
    cmd = [
        exe,
        "-hide_banner",
        "-y",
        "-i",
        str(source_path.resolve()),
        "-map",
        f"0:{stream_index}",
        "-c:s",
        "copy",
        "-f",
        "sup",
        str(output_sup.resolve()),
    ]
    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
        **no_window_kwargs(),
    )
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "ffmpeg PGS extract failed").strip()
        raise RuntimeError(detail)
    if not output_sup.is_file():
        raise RuntimeError("ffmpeg produced no PGS subtitle file")


def resolve_mkvextract(*, mkvmerge: str | None = None) -> str:
    """Resolve mkvextract next to mkvmerge when bundled, else PATH."""
    merge_exe = resolve_executable("mkvmerge") if mkvmerge is None else mkvmerge
    merge_path = Path(merge_exe)
    if merge_path.is_file():
        extract_name = "mkvextract.exe" if merge_path.suffix.lower() == ".exe" else "mkvextract"
        sibling = merge_path.with_name(extract_name)
        if sibling.is_file():
            return str(sibling)
    return shutil.which("mkvextract") or "mkvextract"


def mkv_subtitle_track_ids(source_path: Path, *, mkvmerge: str | None = None) -> list[int]:
    from corrcut.engine.mkv_identify import mkvmerge_identify

    data = mkvmerge_identify(source_path, mkvmerge=mkvmerge)
    return [
        int(track["id"])
        for track in data.get("tracks", [])
        if track.get("type") == "subtitles"
    ]


def subtitle_stream_ordinal(
    subtitle_streams: list[dict[str, Any]],
    stream_index: int,
) -> int | None:
    ordinal = 0
    for stream in subtitle_streams:
        if not isinstance(stream, dict):
            continue
        if int(stream.get("index", -1)) == stream_index:
            return ordinal
        ordinal += 1
    return None


def extract_subtitle_vobsub(
    source_path: Path,
    stream_index: int,
    output_base: Path,
    *,
    subtitle_streams: list[dict[str, Any]] | None = None,
    ffmpeg: str | None = None,
    mkvmerge: str | None = None,
    timeout: int = 600,
) -> tuple[Path, Path]:
    """
    Extract a DVD (VobSub) subtitle stream to a matching .idx/.sub pair.

    Returns (idx_path, sub_path).
    """
    source_path = source_path.resolve()
    output_base = output_base.resolve()
    output_base.parent.mkdir(parents=True, exist_ok=True)

    def _extract_from(extract_source: Path) -> tuple[Path, Path]:
        track_ids = mkv_subtitle_track_ids(extract_source, mkvmerge=mkvmerge)
        if not track_ids:
            raise RuntimeError("No subtitle tracks found for VobSub extraction")

        if subtitle_streams is not None:
            ordinal = subtitle_stream_ordinal(subtitle_streams, stream_index)
            if ordinal is None or ordinal >= len(track_ids):
                raise RuntimeError(f"Subtitle stream #{stream_index} is not a VobSub track")
            track_id = track_ids[ordinal]
        else:
            track_id = track_ids[0]

        extract_exe = resolve_mkvextract(mkvmerge=mkvmerge)
        cmd = [
            extract_exe,
            "tracks",
            str(extract_source),
            f"{track_id}:{output_base}",
        ]
        result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
        **no_window_kwargs(),
    )
        if result.returncode != 0:
            detail = (result.stderr or result.stdout or "mkvextract subtitle extract failed").strip()
            raise RuntimeError(detail)

        idx_path = output_base.with_suffix(".idx")
        sub_path = output_base.with_suffix(".sub")
        if not idx_path.is_file() or not sub_path.is_file():
            raise RuntimeError("mkvextract did not produce VobSub .idx/.sub files")
        return idx_path, sub_path

    if source_path.suffix.lower() in {".mkv", ".mka", ".mks"}:
        return _extract_from(source_path)

    with tempfile.TemporaryDirectory(prefix="ms_vobsub_") as tmp:
        remuxed = Path(tmp) / "subtitle_track.mkv"
        exe = resolve_executable("ffmpeg") if ffmpeg is None else ffmpeg
        cmd = [
            exe,
            "-hide_banner",
            "-y",
            "-i",
            str(source_path),
            "-map",
            f"0:{stream_index}",
            "-c",
            "copy",
            str(remuxed),
        ]
        result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
        **no_window_kwargs(),
    )
        if result.returncode != 0 or not remuxed.is_file():
            detail = (result.stderr or result.stdout or "ffmpeg subtitle remux failed").strip()
            raise RuntimeError(detail)
        return _extract_from(remuxed)


def words_from_project_cues(
    cues: list[dict[str, Any]],
    *,
    sample_rate: int,
) -> list[TranscriptWord]:
    """Tokenize persisted project subtitle cues for lexicon matching."""
    parsed: list[SubtitleCue] = []
    for row in cues:
        start_raw = row.get("start")
        end_raw = row.get("end")
        text = str(row.get("text", "")).strip()
        if not text or not isinstance(start_raw, dict) or not isinstance(end_raw, dict):
            continue
        start = TimePoint.from_json(start_raw)
        end = TimePoint.from_json(end_raw)
        parsed.append(
            SubtitleCue(
                start=Fraction(start.seconds),
                end=Fraction(end.seconds),
                text=text,
            )
        )
    return words_from_cues(parsed, sample_rate=sample_rate)


def load_subtitle_words(
    source_path: Path,
    subtitle_streams: list[dict[str, Any]],
    *,
    preferred_stream_index: int | None,
    audio_language: str | None,
    sample_rate: int,
    log: logging.Logger | None = None,
    project_cues: list[dict[str, Any]] | None = None,
) -> tuple[list[TranscriptWord], int | None]:
    """
    Extract and tokenize a text subtitle track.

    When no embedded text track exists, falls back to ``project_cues`` (e.g. OCR).

    Returns (words, stream_index). Empty words and None index when unavailable.
    """
    emit = log.info if log is not None else logger.info
    stream_index = select_subtitle_stream_index(
        subtitle_streams,
        preferred_index=preferred_stream_index,
        audio_language=audio_language,
    )
    if stream_index is None:
        if project_cues:
            words = words_from_project_cues(project_cues, sample_rate=sample_rate)
            if words:
                emit(f"Loaded {len(words)} word(s) from project subtitle cues (OCR/Whisper)")
                return words, None
        emit("No text subtitle track available for detection")
        return [], None

    with tempfile.TemporaryDirectory(prefix="ms_sub_") as tmp:
        srt_path = Path(tmp) / "subs.srt"
        try:
            extract_subtitle_srt(source_path, stream_index, srt_path)
        except OSError as exc:
            emit(f"Subtitle extract failed: {exc}")
            return [], None
        except RuntimeError as exc:
            emit(f"Subtitle extract failed: {exc}")
            return [], None

        content = srt_path.read_text(encoding="utf-8", errors="replace")
    cues = parse_srt(content)
    if not cues:
        emit(f"Subtitle stream #{stream_index} produced no parseable cues")
        return [], stream_index

    words = words_from_cues(cues, sample_rate=sample_rate)
    emit(f"Loaded {len(words)} word(s) from subtitle stream #{stream_index}")
    return words, stream_index
