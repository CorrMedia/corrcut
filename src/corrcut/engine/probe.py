"""FFprobe-based media intake (Phase 1)."""

from __future__ import annotations

import json
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from corrcut.domain.dialogue_target import DialogueTargetStrategy, strategy_for_layout
from corrcut.engine.jobs.run_process import run_process
from corrcut.engine.toolchain import resolve_executable

# Bump when serialized probe shape changes (invalidates on-disk probe cache).
# 3: video streams include sample/display aspect and rotation.
PROBE_CACHE_VERSION = 3


@dataclass(frozen=True)
class AudioStreamInfo:
    index: int
    codec: str
    channel_layout: str | None
    channels: int
    sample_rate: int | None
    language: str | None
    title: str | None
    is_default: bool
    dialogue_target_strategy: DialogueTargetStrategy


@dataclass(frozen=True)
class VideoStreamInfo:
    index: int
    codec: str
    width: int | None
    height: int | None
    frame_rate: str | None
    sample_aspect_ratio: str | None = None
    display_aspect_ratio: str | None = None
    rotation: int = 0


@dataclass(frozen=True)
class SubtitleStreamInfo:
    index: int
    codec: str
    language: str | None
    title: str | None


@dataclass(frozen=True)
class MediaProbeResult:
    path: Path
    format_name: str
    duration_seconds: float | None
    video_streams: tuple[VideoStreamInfo, ...]
    audio_streams: tuple[AudioStreamInfo, ...]
    subtitle_streams: tuple[SubtitleStreamInfo, ...] = ()

    def summary_lines(self) -> list[str]:
        lines = [
            f"File: {self.path}",
            f"Format: {self.format_name}",
            f"Duration: {self.duration_seconds:.3f}s" if self.duration_seconds else "Duration: unknown",
        ]
        for v in self.video_streams:
            res = f"{v.width}x{v.height}" if v.width and v.height else "unknown size"
            rate = f" @ {v.frame_rate}" if v.frame_rate else ""
            lines.append(f"  Video #{v.index}: {v.codec} {res}{rate}")
        for a in self.audio_streams:
            layout = a.channel_layout or f"{a.channels}ch"
            lang = f" ({a.language})" if a.language else ""
            title = f' "{a.title}"' if a.title else ""
            default = " [default]" if a.is_default else ""
            lines.append(
                f"  Audio #{a.index}: {a.codec} {layout}{lang}{title}{default} "
                f"-> strategy={a.dialogue_target_strategy.value}"
            )
        for s in self.subtitle_streams:
            lang = f" ({s.language})" if s.language else ""
            title = f' "{s.title}"' if s.title else ""
            lines.append(f"  Subtitle #{s.index}: {s.codec}{lang}{title}")
        return lines


def probe_media(
    path: Path,
    ffprobe: str | None = None,
    *,
    cancel_event: threading.Event | None = None,
) -> MediaProbeResult:
    if not path.is_file():
        raise FileNotFoundError(path)

    ffprobe_exe = resolve_executable("ffprobe") if ffprobe is None else ffprobe
    cmd = [
        ffprobe_exe,
        "-v",
        "quiet",
        "-print_format",
        "json",
        "-show_format",
        "-show_streams",
        str(path),
    ]
    result = run_process(cmd, cancel_event=cancel_event, timeout_s=120)
    if result.returncode != 0:
        raise RuntimeError(result.stderr or result.stdout or "ffprobe failed")

    return probe_result_from_ffprobe_json(path, json.loads(result.stdout))


def _stream_disposition_default(stream: dict[str, Any]) -> bool:
    disposition = stream.get("disposition") or {}
    return bool(disposition.get("default"))


def _apply_audio_default_fallback(
    streams: list[AudioStreamInfo],
) -> tuple[AudioStreamInfo, ...]:
    """When no stream has disposition default, treat the first audio as default."""
    if not streams or any(s.is_default for s in streams):
        return tuple(streams)
    first, *rest = streams
    return (
        AudioStreamInfo(
            index=first.index,
            codec=first.codec,
            channel_layout=first.channel_layout,
            channels=first.channels,
            sample_rate=first.sample_rate,
            language=first.language,
            title=first.title,
            is_default=True,
            dialogue_target_strategy=first.dialogue_target_strategy,
        ),
        *rest,
    )


def _aspect_ratio_text(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text or text in {"0:1", "0:0", "N/A"}:
        return None
    return text


def _rotation_degrees(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        return 0
    try:
        degrees = int(round(float(value)))
    except (TypeError, ValueError):
        return 0
    degrees %= 360
    if degrees < 0:
        degrees += 360
    return degrees


def _video_rotation_degrees(stream: dict[str, Any]) -> int:
    """Display rotation in degrees. 90 and 270 swap the picture's aspect."""
    tags = stream.get("tags") or {}
    if isinstance(tags, dict) and tags.get("rotate") is not None:
        return _rotation_degrees(tags.get("rotate"))
    for side in stream.get("side_data_list") or []:
        if isinstance(side, dict) and side.get("rotation") is not None:
            return _rotation_degrees(side.get("rotation"))
    return 0


def probe_result_from_ffprobe_json(path: Path, data: dict[str, Any]) -> MediaProbeResult:
    fmt = data.get("format", {})
    duration_raw = fmt.get("duration")
    duration = float(duration_raw) if duration_raw else None

    video_streams: list[VideoStreamInfo] = []
    audio_streams: list[AudioStreamInfo] = []
    subtitle_streams: list[SubtitleStreamInfo] = []

    for stream in data.get("streams", []):
        codec_type = stream.get("codec_type")
        index = int(stream.get("index", -1))
        codec = stream.get("codec_name", "unknown")

        if codec_type == "video":
            rate = stream.get("r_frame_rate") or stream.get("avg_frame_rate")
            video_streams.append(
                VideoStreamInfo(
                    index=index,
                    codec=codec,
                    width=stream.get("width"),
                    height=stream.get("height"),
                    frame_rate=str(rate) if rate else None,
                    sample_aspect_ratio=_aspect_ratio_text(stream.get("sample_aspect_ratio")),
                    display_aspect_ratio=_aspect_ratio_text(stream.get("display_aspect_ratio")),
                    rotation=_video_rotation_degrees(stream),
                )
            )
        elif codec_type == "audio":
            layout = stream.get("channel_layout")
            channels = int(stream.get("channels", 0))
            rate_raw = stream.get("sample_rate")
            sample_rate = int(rate_raw) if rate_raw else None
            tags = stream.get("tags") or {}
            language = tags.get("language")
            strategy = strategy_for_layout(layout, channels)
            audio_streams.append(
                AudioStreamInfo(
                    index=index,
                    codec=codec,
                    channel_layout=layout,
                    channels=channels,
                    sample_rate=sample_rate,
                    language=language,
                    title=tags.get("title"),
                    is_default=_stream_disposition_default(stream),
                    dialogue_target_strategy=strategy,
                )
            )
        elif codec_type == "subtitle":
            tags = stream.get("tags") or {}
            subtitle_streams.append(
                SubtitleStreamInfo(
                    index=index,
                    codec=codec,
                    language=tags.get("language"),
                    title=tags.get("title"),
                )
            )

    return MediaProbeResult(
        path=path,
        format_name=fmt.get("format_name", "unknown"),
        duration_seconds=duration,
        video_streams=tuple(video_streams),
        audio_streams=_apply_audio_default_fallback(audio_streams),
        subtitle_streams=tuple(subtitle_streams),
    )


def probe_result_to_dict(probe: MediaProbeResult) -> dict[str, object]:
    return {
        "probe_cache_version": PROBE_CACHE_VERSION,
        "path": str(probe.path),
        "format_name": probe.format_name,
        "duration_seconds": probe.duration_seconds,
        "video_streams": [
            {
                "index": v.index,
                "codec": v.codec,
                "width": v.width,
                "height": v.height,
                "frame_rate": v.frame_rate,
                "sample_aspect_ratio": v.sample_aspect_ratio,
                "display_aspect_ratio": v.display_aspect_ratio,
                "rotation": v.rotation,
            }
            for v in probe.video_streams
        ],
        "audio_streams": [
            {
                "index": a.index,
                "codec": a.codec,
                "channel_layout": a.channel_layout,
                "channels": a.channels,
                "sample_rate": a.sample_rate,
                "language": a.language,
                "title": a.title,
                "is_default": a.is_default,
                "dialogue_target_strategy": a.dialogue_target_strategy.value,
            }
            for a in probe.audio_streams
        ],
        "subtitle_streams": [
            {
                "index": s.index,
                "codec": s.codec,
                "language": s.language,
                "title": s.title,
            }
            for s in probe.subtitle_streams
        ],
    }


def probe_result_from_dict(data: dict[str, object]) -> MediaProbeResult:
    path = Path(str(data.get("path", "")))
    video = tuple(
        VideoStreamInfo(
            index=int(v["index"]),
            codec=str(v["codec"]),
            width=int(v["width"]) if v.get("width") is not None else None,
            height=int(v["height"]) if v.get("height") is not None else None,
            frame_rate=str(v["frame_rate"]) if v.get("frame_rate") else None,
            sample_aspect_ratio=_aspect_ratio_text(v.get("sample_aspect_ratio")),
            display_aspect_ratio=_aspect_ratio_text(v.get("display_aspect_ratio")),
            rotation=_rotation_degrees(v.get("rotation")),
        )
        for v in data.get("video_streams", [])
    )
    audio = tuple(
        AudioStreamInfo(
            index=int(a["index"]),
            codec=str(a["codec"]),
            channel_layout=a.get("channel_layout"),
            channels=int(a["channels"]),
            sample_rate=int(a["sample_rate"]) if a.get("sample_rate") else None,
            language=a.get("language"),
            title=a.get("title"),
            is_default=bool(a.get("is_default", False)),
            dialogue_target_strategy=DialogueTargetStrategy(str(a["dialogue_target_strategy"])),
        )
        for a in data.get("audio_streams", [])
    )
    audio = _apply_audio_default_fallback(list(audio))
    subs = tuple(
        SubtitleStreamInfo(
            index=int(s["index"]),
            codec=str(s["codec"]),
            language=s.get("language"),
            title=s.get("title"),
        )
        for s in data.get("subtitle_streams", [])
    )
    duration = data.get("duration_seconds")
    return MediaProbeResult(
        path=path,
        format_name=str(data.get("format_name", "unknown")),
        duration_seconds=float(duration) if duration is not None else None,
        video_streams=video,
        audio_streams=audio,
        subtitle_streams=subs,
    )
