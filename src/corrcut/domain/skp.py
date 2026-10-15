"""Import VideoSkip ``.skp`` skip files into operations.

Format notes (from https://videoskip.herokuapp.com/exchange/howto/edit/ and
the official VideoSkip parser):

* Cues are VTT-like: ``H:MM:SS.xx --> H:MM:SS`` plus a label line.
* Label tokens are category (IMDB Parents Guide prefixes), optional rating
  1–3, optional handling (``audio`` / ``image`` / ``blur`` / ``fast``), and
  optional ``(description)``. First three letters of each token are enough.
* A leading screenshot timestamp (no arrow) and trailing JPEG / offset JSON
  are sync metadata, not edits.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from fractions import Fraction
from pathlib import Path
from typing import Any

from corrcut.domain.corr_categories import (
    OTHER_ID,
    is_known_category_token,
    match_muted_word,
    resolve_categories,
)
from corrcut.domain.edl import parse_edl_time
from corrcut.domain.operation import (
    Operation,
    OperationType,
    default_params,
    is_audio_operation,
)
from corrcut.domain.spatial_keyframes import normalize_box
from corrcut.domain.time_point import TimePoint

_TIME_RANGE = re.compile(r"^([\d:,.]+)\s*-*>\s*([\d:,.]+)\s*$")
_PARENS = re.compile(r"\((.*)\)", re.DOTALL)
_BRACKET = re.compile(r"\[([^\[\]]*)\]")
_WORD = re.compile(r"[a-z]+", re.IGNORECASE)
_RATING = re.compile(r"\b([1-3])\b")

# VideoSkip handling keywords (first three letters are enough).
_AUDIO_HANDLING = frozenset({"aud", "sou", "spe", "wor", "mut"})
_VIDEO_HANDLING = frozenset({"vid", "ima", "img", "bla"})
_BLUR_HANDLING = frozenset({"blu"})
_FAST_HANDLING = frozenset({"fas"})
_HANDLING_PREFIXES = _AUDIO_HANDLING | _VIDEO_HANDLING | _BLUR_HANDLING | _FAST_HANDLING

# IMDB Parents Guide prefixes used by VideoSkip's isSkipped().
_CATEGORY_PREFIXES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("sex", ("sex_with_nudity",)),
    ("nud", ("female_nudity",)),
    ("vio", ("gore", "graphic", "non_graphic")),
    ("gor", ("gore",)),
    ("pro", ("profanity",)),
    ("cur", ("profanity",)),
    ("hat", ("racial_slurs",)),
    ("alc", ("drugs_illegal",)),
    ("dru", ("drugs_illegal",)),
    ("smo", ("drugs_illegal",)),
    ("fri", ("objectionable_scary",)),
    ("sca", ("objectionable_scary",)),
    ("int", ("objectionable_scary",)),
    ("oth", (OTHER_ID,)),
    ("bor", (OTHER_ID,)),
)

_FULL_FRAME = {"x": 0.0, "y": 0.0, "width": 1.0, "height": 1.0}

_MEDIA_EXTS = (".mkv", ".mp4", ".m4v", ".avi", ".mov", ".webm", ".ts", ".m2ts")
_SKIP_SIBLING_EXTS = {".skp", ".edl", ".json"}


@dataclass
class SkpWarning:
    line_number: int
    message: str
    raw: str = ""

    def display(self) -> str:
        if self.line_number > 0:
            return f"Line {self.line_number}: {self.message}"
        return self.message


@dataclass
class SkpImportResult:
    operations: list[Operation] = field(default_factory=list)
    warnings: list[SkpWarning] = field(default_factory=list)


def skp_path(media_path: Path) -> Path:
    return media_path.with_suffix(".skp")


def media_path_for_skp(skp_file: Path) -> Path | None:
    stem = skp_file.stem
    parent = skp_file.parent
    for ext in _MEDIA_EXTS:
        candidate = parent / f"{stem}{ext}"
        if candidate.is_file():
            return candidate
    for candidate in sorted(parent.glob(f"{stem}.*")):
        if candidate.suffix.lower() in _SKIP_SIBLING_EXTS:
            continue
        if candidate.name.endswith(".corr.json"):
            continue
        if candidate.is_file():
            return candidate
    return None


def load_skp_file(
    path: Path,
    *,
    default_stream_index: int,
    default_channels: list[str] | None = None,
) -> SkpImportResult:
    text = path.read_text(encoding="utf-8-sig")
    return parse_skp_text(
        text,
        default_stream_index=default_stream_index,
        default_channels=default_channels,
    )


def parse_skp_time(token: str) -> float | None:
    return parse_edl_time(token.replace(",", "."))


def parse_skp_text(
    text: str,
    *,
    default_stream_index: int,
    default_channels: list[str] | None = None,
) -> SkpImportResult:
    result = SkpImportResult()
    channels = list(default_channels or [])
    body, offsets = _split_skp_payload(text)
    _warn_nonzero_offsets(offsets, result)

    lines = body.splitlines()
    index = 0
    saw_range = False
    while index < len(lines):
        raw = lines[index]
        stripped = raw.strip()
        if not stripped or stripped.startswith("#") or stripped.startswith(";"):
            index += 1
            continue
        matched = _TIME_RANGE.match(stripped)
        if matched is None:
            if parse_skp_time(stripped) is not None:
                index = _skip_sync_block(lines, index)
                continue
            result.warnings.append(
                SkpWarning(index + 1, "unrecognized line; skipped.", raw)
            )
            index += 1
            continue

        saw_range = True
        time_number = index + 1
        start = parse_skp_time(matched.group(1))
        end = parse_skp_time(matched.group(2))
        label, label_number, index = _collect_label(lines, index + 1)
        _add_skp_operation(
            start=start,
            end=end,
            label=label,
            time_line=raw,
            time_number=time_number,
            label_number=label_number,
            default_stream_index=default_stream_index,
            default_channels=channels,
            result=result,
        )

    if not saw_range and not result.operations:
        result.warnings.append(
            SkpWarning(
                0,
                "No VideoSkip time ranges were found. Expected lines like "
                "0:14:08.27 --> 0:14:14 followed by a category label.",
            )
        )
    return result


def _split_skp_payload(text: str) -> tuple[str, dict[str, Any] | None]:
    body = text.replace("\r\n", "\n").replace("\r", "\n")
    marker = "data:image/"
    at = body.find(marker)
    if at >= 0:
        body = body[:at]
    brace = body.find("{")
    offsets: dict[str, Any] | None = None
    if brace >= 0:
        raw = body[brace:].strip()
        body = body[:brace]
        offsets = _parse_offsets(raw)
    return body, offsets


def _parse_offsets(raw: str) -> dict[str, Any] | None:
    try:
        parsed, _ = json.JSONDecoder().raw_decode(raw)
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


def _warn_nonzero_offsets(
    offsets: dict[str, Any] | None, result: SkpImportResult
) -> None:
    if not offsets:
        return
    parts: list[str] = []
    for key, value in offsets.items():
        if key == "local":
            continue
        try:
            seconds = float(value)
        except (TypeError, ValueError):
            continue
        if abs(seconds) > 1e-9:
            parts.append(f"{key} {seconds:+.2f}s")
    if parts:
        result.warnings.append(
            SkpWarning(
                0,
                "Service offsets were not applied ("
                + ", ".join(parts)
                + "). Times may need syncing.",
            )
        )


def _skip_sync_block(lines: list[str], index: int) -> int:
    nxt = index + 1
    if nxt < len(lines) and lines[nxt].strip() and _TIME_RANGE.match(lines[nxt].strip()) is None:
        if parse_skp_time(lines[nxt].strip()) is None:
            return nxt + 1
    return nxt


def _collect_label(lines: list[str], start: int) -> tuple[str, int, int]:
    parts: list[str] = []
    label_number = start + 1
    index = start
    while index < len(lines):
        stripped = lines[index].strip()
        if not stripped:
            break
        if _TIME_RANGE.match(stripped) is not None:
            break
        if parse_skp_time(stripped) is not None and "-->" not in stripped:
            break
        if not parts:
            label_number = index + 1
        parts.append(stripped)
        index += 1
    return " ".join(parts), label_number, index


def _add_skp_operation(
    *,
    start: float | None,
    end: float | None,
    label: str,
    time_line: str,
    time_number: int,
    label_number: int,
    default_stream_index: int,
    default_channels: list[str],
    result: SkpImportResult,
) -> None:
    raw = time_line if not label else f"{time_line.strip()} / {label}"
    if start is None or end is None:
        result.warnings.append(
            SkpWarning(time_number, "could not parse start/end times.", time_line)
        )
        return
    if end <= start:
        result.warnings.append(
            SkpWarning(time_number, "end must be greater than start; skipped.", time_line)
        )
        return
    if not label.strip():
        result.warnings.append(
            SkpWarning(time_number, "missing category label; imported as other.", time_line)
        )

    disabled = "//" in label
    action, box_raw, box_warning = _map_handling(label)
    categories, description, category_warning = _map_label(label, action)
    severity = _severity_from_label(label)

    if action == "fast":
        result.warnings.append(
            SkpWarning(
                label_number,
                "VideoSkip 'fast' has no corr.json equivalent; imported as skip.",
                raw,
            )
        )
        action = "skip"

    if category_warning:
        result.warnings.append(SkpWarning(label_number, category_warning, raw))
    if box_warning:
        result.warnings.append(SkpWarning(label_number, box_warning, raw))

    op_type = {
        "mute": OperationType.MUTE,
        "blank": OperationType.BLANK,
        "cover": OperationType.COVER,
        "blur": OperationType.BLUR,
        "skip": OperationType.SKIP,
    }[action]

    params = _params_for_action(op_type, box_raw)
    audio_channels = default_channels if is_audio_operation(op_type) else []
    op = Operation.create(
        op_type,
        default_stream_index,
        audio_channels,
        TimePoint(Fraction(start).limit_denominator(100_000)),
        TimePoint(Fraction(end).limit_denominator(100_000)),
        params=params,
        order=len(result.operations),
        categories=categories,
        description=description,
        severity=severity,
        source="skp",
    )
    if disabled:
        op.enabled = False
        result.warnings.append(
            SkpWarning(label_number, "commented out with //; imported disabled.", raw)
        )
    result.operations.append(op)


def _map_handling(label: str) -> tuple[str, str | None, str | None]:
    cleaned, box_raw = _strip_notes(label)
    prefixes = {token[:3] for token in _WORD.findall(cleaned.lower()) if len(token) >= 3}
    if prefixes & _AUDIO_HANDLING:
        return "mute", None, None
    if prefixes & _VIDEO_HANDLING:
        if box_raw:
            return "cover", box_raw, _invalid_box_warning(box_raw)
        return "blank", None, None
    if prefixes & _BLUR_HANDLING:
        return "blur", box_raw, _invalid_box_warning(box_raw)
    if prefixes & _FAST_HANDLING:
        return "fast", None, None
    return "skip", None, None


def _invalid_box_warning(box_raw: str | None) -> str | None:
    if box_raw and _box_from_skp(box_raw) is None:
        return "blur box could not be parsed; using the full frame."
    return None


def _strip_notes(label: str) -> tuple[str, str | None]:
    box_raw = None
    bracket = _BRACKET.search(label)
    if bracket:
        box_raw = bracket.group(0)
    without_box = _BRACKET.sub(" ", label)
    without_parens = _PARENS.sub(" ", without_box)
    return re.sub(r"\s+", " ", without_parens).strip(), box_raw


def _severity_from_label(label: str) -> int | None:
    """VideoSkip optional rating 1–3 on the label line."""
    cleaned, _box = _strip_notes(label)
    match = _RATING.search(cleaned)
    if match is None:
        return None
    return int(match.group(1))


def _map_label(
    label: str, action: str
) -> tuple[list[str] | None, str | None, str | None]:
    description = _description_from_label(label)
    cleaned, _box = _strip_notes(label)
    tokens = _WORD.findall(cleaned.lower())
    catalog: list[str] = []
    leftover: list[str] = []
    for token in tokens:
        prefix = token[:3]
        if prefix in _HANDLING_PREFIXES:
            continue
        if is_known_category_token(token):
            for cid in resolve_categories([token]):
                if cid not in catalog:
                    catalog.append(cid)
            continue
        mapped = _category_from_prefix(prefix)
        if mapped:
            for cid in mapped:
                if cid not in catalog:
                    catalog.append(cid)
            continue
        leftover.append(token)

    if not catalog:
        warning = None
        if leftover or not tokens:
            shown = leftover[0] if leftover else "(empty)"
            warning = f"'{shown}' is not a VideoSkip/catalog category; stored as other."
        return [OTHER_ID], description, warning

    catalog = _refine_nudity(catalog, label)
    if action == "mute" and description:
        leaf = match_muted_word(description, preferred_parent="profanity")
        if leaf:
            catalog = [leaf]
    return catalog, description, None


def _category_from_prefix(prefix: str) -> tuple[str, ...] | None:
    for key, ids in _CATEGORY_PREFIXES:
        if prefix == key:
            return ids
    return None


def _refine_nudity(catalog: list[str], label: str) -> list[str]:
    if "female_nudity" not in catalog:
        return catalog
    text = label.lower()
    has_female = bool(re.search(r"\bfemale\b|\bwoman\b|\bwomen\b", text))
    has_male = bool(re.search(r"\bmale\b|\bman\b|\bmen\b", text))
    if has_male and not has_female:
        return ["male_nudity" if cid == "female_nudity" else cid for cid in catalog]
    return catalog


def _description_from_label(label: str) -> str | None:
    match = _PARENS.search(label)
    if not match:
        return None
    text = match.group(1).strip()
    return text or None


def _params_for_action(op_type: OperationType, box_raw: str | None) -> dict[str, Any] | None:
    if op_type == OperationType.COVER:
        box = _box_from_skp(box_raw) or dict(_FULL_FRAME)
        return {"box": box}
    if op_type == OperationType.BLUR:
        params = default_params(OperationType.BLUR)
        params["box"] = _box_from_skp(box_raw) or dict(_FULL_FRAME)
        return params
    return None


def _box_from_skp(raw: str | None) -> dict[str, float] | None:
    if not raw:
        return None
    try:
        values = json.loads(raw)
    except json.JSONDecodeError:
        return None
    if not isinstance(values, list) or len(values) != 4:
        return None
    try:
        x1, y1, x2, y2 = (float(item) for item in values)
    except (TypeError, ValueError):
        return None
    scale = 1.0 if max(abs(x1), abs(y1), abs(x2), abs(y2)) <= 1.5 else 100.0
    return normalize_box(
        {
            "x": min(x1, x2) / scale,
            "y": min(y1, y2) / scale,
            "width": abs(x2 - x1) / scale,
            "height": abs(y2 - y1) / scale,
        },
        overflow=True,
    )
