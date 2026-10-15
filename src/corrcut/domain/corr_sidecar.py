"""Serialize / deserialize Jellyfin ``{stem}.corr.json`` sidecars."""

from __future__ import annotations

import json
from fractions import Fraction
from pathlib import Path
from typing import Any

from corrcut.domain.audio_language import (
    normalize_audio_language,
    resolve_track_language,
)
from corrcut.domain.corr_categories import resolve_categories
from corrcut.domain.operation import (
    DEFAULT_BEEP_FREQ,
    DEFAULT_BEEP_GAIN,
    DEFAULT_BLUR_RADIUS,
    DEFAULT_PIXEL_SIZE,
    DEFAULT_VOLUME_GAIN,
    DEFAULT_ZOOM_SCALE,
    ITEM_LEVEL_STREAM_INDEX,
    Operation,
    OperationType,
    is_audio_operation,
    is_item_level_operation,
    normalize_operation_type,
    parse_severity,
)
from corrcut.domain.project import Project, track_for_edit_language
from corrcut.domain.spatial_keyframes import (
    expand_spatial_operation,
    merge_abutting_spatial_operations,
    normalize_box,
)
from corrcut.domain.time_point import TimePoint
from corrcut.io.atomic_write import atomic_write_text

SCHEMA_VERSION = "1.0"
_PLAYBACK_ACTIONS = frozenset(
    {
        "mute",
        "volume",
        "beep",
        "skip",
        "blank",
        "cover",
        "blur",
        "pixelate",
        "zoom",
        "crop",
        "punch",
        "boxblur",
        "blackout",
        "mosaic",
    }
)


def sidecar_path(media_path: Path) -> Path:
    """``MyMovie.mkv`` → ``MyMovie.corr.json`` (plugin CorrFile.GetPath)."""
    return media_path.with_name(media_path.stem + ".corr.json")


def media_path_for_sidecar(corr_path: Path) -> Path | None:
    """Best-effort sibling media path for a ``.corr.json`` file."""
    name = corr_path.name
    if not name.endswith(".corr.json"):
        return None
    stem = name[: -len(".corr.json")]
    parent = corr_path.parent
    for ext in (".mkv", ".mp4", ".m4v", ".avi", ".mov", ".webm", ".ts", ".m2ts"):
        candidate = parent / f"{stem}{ext}"
        if candidate.is_file():
            return candidate
    matches = sorted(parent.glob(f"{stem}.*"))
    for candidate in matches:
        if candidate.suffix.lower() not in {".json", ".corr"} and candidate.is_file():
            if candidate.name.endswith(".corr.json"):
                continue
            return candidate
    return None


def load_corr_file(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as fh:
        data = json.load(fh)
    if not isinstance(data, dict):
        raise ValueError("corr.json root must be an object")
    return data


def save_corr_file(path: Path, document: dict[str, Any]) -> None:
    text = json.dumps(document, indent=2, ensure_ascii=False) + "\n"
    atomic_write_text(path, text, newline="\n")


def operations_to_corr_document(project: Project) -> dict[str, Any]:
    edits: list[dict[str, Any]] = []
    preserve = bool(project.preserve_letterbox_on_zoom)
    for op in sorted(project.operations, key=lambda o: (o.order, o.time_range.start.seconds)):
        if not op.enabled:
            continue
        for segment in expand_spatial_operation(op):
            language = project.operation_language(op) if is_audio_operation(op.type) else None
            edits.append(
                operation_to_corr_edit(
                    segment,
                    preserve_letterbox=preserve,
                    language=language,
                )
            )
    document: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "edits": edits,
    }
    media = _media_metadata(project)
    if media:
        document["media"] = media
    if preserve and project.video_matte is not None and not project.video_matte.is_trivial():
        box = project.video_matte.as_box()
        document["video_matte"] = {
            "x": float(box["x"]),
            "y": float(box["y"]),
            "width": float(box["width"]),
            "height": float(box["height"]),
        }
    return document


def operations_from_corr_document(
    data: dict[str, Any],
    *,
    default_stream_index: int,
    tracks: list[Any] | None = None,
) -> list[Operation]:
    raw_edits = data.get("edits")
    if not isinstance(raw_edits, list):
        return []
    operations: list[Operation] = []
    for index, raw in enumerate(raw_edits):
        if not isinstance(raw, dict):
            continue
        stream_index, language = _audio_target_from_corr(
            raw,
            default_stream_index=default_stream_index,
            tracks=tracks or [],
        )
        op = operation_from_corr_edit(
            raw,
            default_stream_index=stream_index,
            order=index,
            language=language,
        )
        if op is not None:
            operations.append(op)
    return merge_abutting_spatial_operations(operations)


def operation_to_corr_edit(
    op: Operation,
    *,
    preserve_letterbox: bool = False,
    language: str | None = None,
) -> dict[str, Any]:
    start = float(op.time_range.start.seconds)
    end = float(op.time_range.end.seconds)
    edit: dict[str, Any] = {
        "id": op.id,
        "start": start,
        "end": end,
        "action": op.type.value,
    }
    if op.description:
        edit["description"] = op.description
    if op.severity is not None:
        edit["severity"] = op.severity
    edit["categories"] = resolve_categories(op.categories)

    params = op.params
    if op.type in {OperationType.MUTE, OperationType.VOLUME, OperationType.BEEP}:
        channels = [c.strip().upper() for c in op.scope.channels if c and c.strip()]
        if channels:
            edit["channels"] = channels
        if language:
            edit["language"] = language
    if op.type == OperationType.VOLUME:
        edit["gain"] = float(params.get("gain", DEFAULT_VOLUME_GAIN))
    if op.type == OperationType.BEEP:
        edit["frequency"] = float(params.get("beep_freq", params.get("frequency", DEFAULT_BEEP_FREQ)))
        edit["gain"] = float(params.get("gain", DEFAULT_BEEP_GAIN))
    if op.type in {OperationType.ZOOM, OperationType.CROP}:
        _emit_zoom_crop(edit, params)
        if op.type == OperationType.ZOOM and preserve_letterbox:
            edit["preserve_letterbox"] = True
    if op.type == OperationType.BLUR:
        edit["radius"] = float(params.get("radius", DEFAULT_BLUR_RADIUS))
        _emit_box_fields(edit, params)
    if op.type == OperationType.PIXELATE:
        edit["size"] = float(params.get("size", DEFAULT_PIXEL_SIZE))
        _emit_box_fields(edit, params)
    if op.type == OperationType.COVER:
        _emit_box_fields(edit, params)
    return edit


def operation_from_corr_edit(
    raw: dict[str, Any],
    *,
    default_stream_index: int,
    order: int = 0,
    language: str | None = None,
) -> Operation | None:
    action = raw.get("action")
    if not isinstance(action, str) or action.strip().lower() not in _PLAYBACK_ACTIONS:
        return None
    try:
        start = float(raw["start"])
        end = float(raw["end"])
    except (KeyError, TypeError, ValueError):
        return None
    if end <= start:
        return None

    op_type = normalize_operation_type(action)
    params = _params_from_corr_edit(op_type, raw)
    channels = _channels_from_corr(raw.get("channels"))
    stream_index = (
        ITEM_LEVEL_STREAM_INDEX if is_item_level_operation(op_type) else default_stream_index
    )
    categories = [str(c) for c in raw.get("categories", []) if str(c).strip()]
    description = raw.get("description")
    edit_id = raw.get("id")
    op = Operation.create(
        op_type,
        stream_index,
        channels if op_type in {OperationType.MUTE, OperationType.VOLUME, OperationType.BEEP} else [],
        TimePoint(Fraction(start).limit_denominator(100_000)),
        TimePoint(Fraction(end).limit_denominator(100_000)),
        params=params,
        order=order,
        categories=categories,
        description=str(description).strip() if description else None,
        severity=parse_severity(raw.get("severity")),
        language=language if is_audio_operation(op_type) else None,
    )
    if isinstance(edit_id, str) and edit_id.strip():
        op.id = edit_id.strip()
    return op


def _params_from_corr_edit(op_type: OperationType, raw: dict[str, Any]) -> dict[str, Any]:
    params: dict[str, Any] = {}
    if op_type == OperationType.VOLUME:
        gain = raw.get("gain", DEFAULT_VOLUME_GAIN)
        params["gain"] = max(0.0, min(1.0, float(gain)))
    elif op_type == OperationType.BEEP:
        freq = raw.get("frequency", DEFAULT_BEEP_FREQ)
        params["beep_freq"] = float(freq) if float(freq) > 0 else DEFAULT_BEEP_FREQ
        gain = raw.get("gain", DEFAULT_BEEP_GAIN)
        params["gain"] = max(0.0, min(1.0, float(gain)))
    elif op_type in {OperationType.ZOOM, OperationType.CROP}:
        scale = raw.get("scale", DEFAULT_ZOOM_SCALE)
        params["scale"] = float(scale) if float(scale) > 1 else DEFAULT_ZOOM_SCALE
        if raw.get("box"):
            box = _normalize_box(raw["box"])
            if box:
                params["box"] = box
            box_end = _normalize_box_end(raw.get("box_end"), box)
            if box_end:
                params["box_end"] = box_end
        else:
            params["x"] = _clamp01(raw.get("x", 0.5))
            params["y"] = _clamp01(raw.get("y", 0.5))
            if raw.get("x_end") is not None:
                params["x_end"] = _clamp01(raw["x_end"])
            if raw.get("y_end") is not None:
                params["y_end"] = _clamp01(raw["y_end"])
    elif op_type == OperationType.BLUR:
        radius = raw.get("radius", DEFAULT_BLUR_RADIUS)
        params["radius"] = float(radius) if float(radius) > 0 else DEFAULT_BLUR_RADIUS
        box = _normalize_box(raw.get("box"), overflow=True)
        if box:
            params["box"] = box
            box_end = _normalize_box_end(raw.get("box_end"), box, overflow=True)
            if box_end:
                params["box_end"] = box_end
    elif op_type == OperationType.PIXELATE:
        size = raw.get("size", DEFAULT_PIXEL_SIZE)
        size_f = float(size) if float(size) > 1 else DEFAULT_PIXEL_SIZE
        params["size"] = max(2.0, min(128.0, size_f))
        box = _normalize_box(raw.get("box"), overflow=True)
        if box:
            params["box"] = box
            box_end = _normalize_box_end(raw.get("box_end"), box, overflow=True)
            if box_end:
                params["box_end"] = box_end
    elif op_type == OperationType.COVER:
        box = _normalize_box(raw.get("box"), overflow=True)
        if box:
            params["box"] = box
            box_end = _normalize_box_end(raw.get("box_end"), box, overflow=True)
            if box_end:
                params["box_end"] = box_end
    return params


def _emit_zoom_crop(edit: dict[str, Any], params: dict[str, Any]) -> None:
    if params.get("box"):
        _emit_box_fields(edit, params)
        return
    edit["scale"] = float(params.get("scale", DEFAULT_ZOOM_SCALE))
    edit["x"] = float(params.get("x", 0.5))
    edit["y"] = float(params.get("y", 0.5))
    if params.get("x_end") is not None:
        edit["x_end"] = float(params["x_end"])
    if params.get("y_end") is not None:
        edit["y_end"] = float(params["y_end"])


def _emit_box_fields(edit: dict[str, Any], params: dict[str, Any]) -> None:
    box = params.get("box")
    if isinstance(box, dict):
        edit["box"] = {
            "x": float(box["x"]),
            "y": float(box["y"]),
            "width": float(box["width"]),
            "height": float(box["height"]),
        }
    box_end = params.get("box_end")
    if isinstance(box_end, dict) and box:
        end: dict[str, float] = {
            "x": float(box_end.get("x", box["x"])),
            "y": float(box_end.get("y", box["y"])),
        }
        width = box_end.get("width")
        height = box_end.get("height")
        if width is not None and abs(float(width) - float(box["width"])) > 1e-9:
            end["width"] = float(width)
        if height is not None and abs(float(height) - float(box["height"])) > 1e-9:
            end["height"] = float(height)
        edit["box_end"] = end


def _audio_target_from_corr(
    raw: dict[str, Any],
    *,
    default_stream_index: int,
    tracks: list[Any],
) -> tuple[int, str | None]:
    action = raw.get("action")
    if not isinstance(action, str) or action.strip().lower() not in {"mute", "volume", "beep", "duck", "gain"}:
        return default_stream_index, None
    tagged = raw.get("language")
    code = normalize_audio_language(str(tagged)) if isinstance(tagged, str) else None
    stream_index = default_stream_index
    if code is not None:
        match = track_for_edit_language(tracks, code)
        if match is not None:
            stream_index = match.stream_index
    else:
        for track in tracks:
            if getattr(track, "stream_index", None) == default_stream_index:
                code = resolve_track_language(
                    getattr(track, "language", None),
                    getattr(track, "title", None),
                )[0]
                break
        if code is None:
            code = resolve_track_language(None)[0]
    return stream_index, code


def _channels_from_corr(raw: Any) -> list[str]:
    if not isinstance(raw, list):
        return []
    seen: set[str] = set()
    out: list[str] = []
    for item in raw:
        if not isinstance(item, str) or not item.strip():
            continue
        name = item.strip().upper()
        if name not in seen:
            seen.add(name)
            out.append(name)
    return out


def _normalize_box(raw: Any, *, overflow: bool = False) -> dict[str, float] | None:
    return normalize_box(raw, overflow=overflow)


def _normalize_box_end(
    raw: Any,
    start: dict[str, float] | None,
    *,
    overflow: bool = False,
) -> dict[str, float] | None:
    if not isinstance(raw, dict) or start is None:
        return None
    try:
        merged = {
            "x": float(raw.get("x", start["x"])),
            "y": float(raw.get("y", start["y"])),
            "width": float(raw["width"]) if raw.get("width") is not None else float(start["width"]),
            "height": (
                float(raw["height"]) if raw.get("height") is not None else float(start["height"])
            ),
        }
    except (TypeError, ValueError, KeyError):
        return None
    return normalize_box(merged, overflow=overflow)


def _clamp01(value: Any) -> float:
    return max(0.0, min(1.0, float(value)))


def _media_metadata(project: Project) -> dict[str, Any] | None:
    if project.source is None:
        return None
    media: dict[str, Any] = {}
    probe = project.source.probe or {}
    format_info = probe.get("format") if isinstance(probe, dict) else None
    if isinstance(format_info, dict):
        duration = format_info.get("duration")
        if duration is not None:
            try:
                media["runtime"] = float(duration)
            except (TypeError, ValueError):
                pass
    video_streams = probe.get("video_streams") if isinstance(probe, dict) else None
    if isinstance(video_streams, list) and video_streams:
        first = video_streams[0]
        if isinstance(first, dict) and first.get("avg_frame_rate"):
            fps = _parse_fps(str(first["avg_frame_rate"]))
            if fps:
                media["fps"] = fps
    title = project.source.path.stem
    if title:
        media["title"] = title
    return media or None


def _parse_fps(raw: str) -> float | None:
    if "/" in raw:
        num_s, den_s = raw.split("/", 1)
        try:
            num, den = float(num_s), float(den_s)
        except ValueError:
            return None
        if den == 0:
            return None
        return num / den
    try:
        return float(raw)
    except ValueError:
        return None
