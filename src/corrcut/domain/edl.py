"""Import classic CorrMedia / Comskip ``.edl`` files into operations."""

from __future__ import annotations

import shlex
from dataclasses import dataclass, field
from fractions import Fraction
from pathlib import Path

from corrcut.domain.corr_categories import (
    is_known_category_token,
    resolve_categories,
)
from corrcut.domain.operation import (
    Operation,
    is_audio_operation,
    is_spatial_operation,
    normalize_operation_type,
)
from corrcut.domain.time_point import TimePoint

# Classic EDL numbers (CorrMedia + Comskip). Type 2 is a scene marker — no corr action.
_NUMERIC_ACTIONS: dict[str, str | None] = {
    "0": "skip",  # Comskip cut
    "1": "mute",
    "2": None,  # scene marker
    "3": "skip",
}

_NAMED_UNMAPPED = frozenset(
    {
        "scene",
        "scene_marker",
        "marker",
        "poi",
        "pause",
        "none",
        "commercial",
        "intro",
        "credits",
    }
)

_ACTION_ALIASES = {
    "cut": "skip",
    "mute": "mute",
    "skip": "skip",
    "volume": "volume",
    "duck": "volume",
    "gain": "volume",
    "beep": "beep",
    "blank": "blank",
    "cover": "cover",
    "blackout": "cover",
    "blur": "blur",
    "boxblur": "blur",
    "pixelate": "pixelate",
    "mosaic": "pixelate",
    "zoom": "zoom",
    "punch": "zoom",
    "crop": "crop",
}

_CMX_HINT = ("title:", "fcm:")


@dataclass
class EdlWarning:
    line_number: int
    message: str
    raw: str = ""

    def display(self) -> str:
        if self.line_number > 0:
            return f"Line {self.line_number}: {self.message}"
        return self.message


@dataclass
class EdlImportResult:
    operations: list[Operation] = field(default_factory=list)
    warnings: list[EdlWarning] = field(default_factory=list)
    looks_like_cmx: bool = False


def edl_path(media_path: Path) -> Path:
    return media_path.with_suffix(".edl")


def media_path_for_edl(edl_path: Path) -> Path | None:
    stem = edl_path.stem
    parent = edl_path.parent
    for ext in (".mkv", ".mp4", ".m4v", ".avi", ".mov", ".webm", ".ts", ".m2ts"):
        candidate = parent / f"{stem}{ext}"
        if candidate.is_file():
            return candidate
    for candidate in sorted(parent.glob(f"{stem}.*")):
        if candidate.suffix.lower() in {".edl", ".json"}:
            continue
        if candidate.name.endswith(".corr.json"):
            continue
        if candidate.is_file():
            return candidate
    return None


def load_edl_file(
    path: Path,
    *,
    default_stream_index: int,
    default_channels: list[str] | None = None,
) -> EdlImportResult:
    text = path.read_text(encoding="utf-8-sig")
    return parse_edl_text(
        text,
        default_stream_index=default_stream_index,
        default_channels=default_channels,
    )


def parse_edl_text(
    text: str,
    *,
    default_stream_index: int,
    default_channels: list[str] | None = None,
) -> EdlImportResult:
    result = EdlImportResult()
    channels = list(default_channels or [])
    for index, raw_line in enumerate(text.splitlines(), start=1):
        trimmed = raw_line.strip()
        if not trimmed:
            continue
        lower = trimmed.lower()
        if any(lower.startswith(hint) for hint in _CMX_HINT):
            result.looks_like_cmx = True
            result.warnings.append(
                EdlWarning(
                    index,
                    "CMX header found; this is not a CorrMedia/Comskip EDL line.",
                    raw_line,
                )
            )
            continue
        if trimmed.startswith("#") or trimmed.startswith(";"):
            continue
        _parse_edl_line(
            trimmed,
            line_number=index,
            default_stream_index=default_stream_index,
            default_channels=channels,
            result=result,
        )
    if result.looks_like_cmx and not result.operations:
        result.warnings.insert(
            0,
            EdlWarning(
                0,
                "This looks like a CMX 3600 EDL. Import supports CorrMedia/Comskip "
                "lines: start_seconds end_time [action] [category] [description].",
            ),
        )
    return result


def parse_edl_time(token: str) -> float | None:
    text = token.strip()
    if not text:
        return None
    parts = text.split(":")
    if len(parts) == 1:
        try:
            value = float(text)
        except ValueError:
            return None
        return value if value >= 0 else None
    if len(parts) == 4:
        return None
    try:
        pieces = [float(p) for p in parts]
    except ValueError:
        return None
    if any(p < 0 for p in pieces):
        return None
    if len(parts) == 2:
        minutes, seconds = pieces
        return minutes * 60.0 + seconds
    if len(parts) == 3:
        hours, minutes, seconds = pieces
        return hours * 3600.0 + minutes * 60.0 + seconds
    return None


def _split_line(line: str) -> list[str]:
    try:
        parts = shlex.split(line, posix=True)
    except ValueError:
        parts = line.split()
    return [part for part in parts if part]


def _parse_edl_line(
    line: str,
    *,
    line_number: int,
    default_stream_index: int,
    default_channels: list[str],
    result: EdlImportResult,
) -> None:
    parts = _split_line(line)
    if len(parts) < 2:
        result.warnings.append(EdlWarning(line_number, "need at least start and end times.", line))
        return
    start = parse_edl_time(parts[0])
    end = parse_edl_time(parts[1])
    if start is None or end is None:
        if parts[0].count(":") == 3 or parts[1].count(":") == 3:
            result.warnings.append(
                EdlWarning(
                    line_number,
                    "frame timecode is not mapped; use seconds or HH:MM:SS.",
                    line,
                )
            )
            return
        result.warnings.append(EdlWarning(line_number, "could not parse start/end times.", line))
        return
    if end <= start:
        result.warnings.append(
            EdlWarning(line_number, "end must be greater than start; skipped.", line)
        )
        return

    action_token = parts[2] if len(parts) >= 3 else "3"
    mapped = _map_action(action_token)
    if mapped is None:
        kind = action_token.strip().lower()
        if kind in {"2", "scene", "scene_marker", "marker"}:
            message = "scene marker has no corr.json equivalent; skipped."
        elif kind == "pause":
            message = "pause is not supported; skipped."
        else:
            message = f"action '{action_token}' has no corr.json equivalent; skipped."
        result.warnings.append(EdlWarning(line_number, message, line))
        return

    rest = parts[3:]
    categories: list[str] | None = None
    description: str | None = None
    if rest and is_known_category_token(rest[0]):
        categories = resolve_categories([rest[0]])
        rest = rest[1:]
    elif rest:
        result.warnings.append(
            EdlWarning(
                line_number,
                f"'{rest[0]}' is not a catalog category; stored as description.",
                line,
            )
        )
    if rest:
        description = " ".join(rest)

    try:
        op_type = normalize_operation_type(mapped)
    except ValueError:
        result.warnings.append(
            EdlWarning(
                line_number,
                f"action '{action_token}' has no corr.json equivalent; skipped.",
                line,
            )
        )
        return

    audio_channels = default_channels if is_audio_operation(op_type) else []
    op = Operation.create(
        op_type,
        default_stream_index,
        audio_channels,
        TimePoint(Fraction(start).limit_denominator(100_000)),
        TimePoint(Fraction(end).limit_denominator(100_000)),
        order=len(result.operations),
        categories=categories,
        description=description,
        source="edl",
    )
    if is_spatial_operation(op_type):
        result.warnings.append(
            EdlWarning(
                line_number,
                f"{op_type.value} has no box/position in EDL; using the default region.",
                line,
            )
        )
    result.operations.append(op)


def _map_action(token: str) -> str | None:
    key = token.strip().lower()
    if key in _NUMERIC_ACTIONS:
        return _NUMERIC_ACTIONS[key]
    if key in _NAMED_UNMAPPED:
        return None
    if key in _ACTION_ALIASES:
        return _ACTION_ALIASES[key]
    try:
        return normalize_operation_type(key).value
    except ValueError:
        return None
