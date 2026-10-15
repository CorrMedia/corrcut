"""Keyframe poses for spatial video ops (cover/blur/pixelate/zoom/crop).

Authoring stores poses at absolute master-timeline seconds. The edit region
is only the enable window: hold before the first pose, lerp between poses,
hold after the last. Moving or trimming the region never retimes keys.

corr.json only supports a start box plus optional ``box_end``, so export
expands 3+ playback poses into abutting two-pose edits; import merges that
chain back into one operation.
"""

from __future__ import annotations

from collections.abc import Sequence
from copy import deepcopy
from dataclasses import dataclass, replace
from fractions import Fraction
from typing import Any

from corrcut.domain.operation import (
    DEFAULT_BOX,
    DEFAULT_ZOOM_SCALE,
    Operation,
    OperationType,
    TimeRange,
    is_spatial_operation,
)
from corrcut.domain.time_point import TimePoint

OFFSET_EPS = 1e-4
BOX_EPS = 2e-3
TIME_EPS = 1e-3
MIN_VISIBLE_BOX = 0.02
_MAX_BOX_SPAN = 2.0

_BOX_TYPES = frozenset({OperationType.COVER, OperationType.BLUR, OperationType.PIXELATE})
_ZOOM_TYPES = frozenset({OperationType.ZOOM, OperationType.CROP})


@dataclass(frozen=True)
class SpatialKeyframe:
    """Pose at an absolute timeline time (seconds)."""

    seconds: float
    box: dict[str, float]

    def as_box(self) -> dict[str, float]:
        return copy_box(self.box)


def copy_box(box: dict[str, float]) -> dict[str, float]:
    return {
        "x": float(box["x"]),
        "y": float(box["y"]),
        "width": float(box["width"]),
        "height": float(box["height"]),
    }


def box_from_center_scale(cx: float, cy: float, scale: float) -> dict[str, float]:
    width = min(1.0, 1.0 / max(1.01, float(scale)))
    height = width
    return {
        "x": max(0.0, min(1.0 - width, float(cx) - width / 2)),
        "y": max(0.0, min(1.0 - height, float(cy) - height / 2)),
        "width": width,
        "height": height,
    }


def lock_box_to_source_aspect(box: dict[str, float]) -> dict[str, float]:
    """Force normalized width == height so punch-in matches the source ratio."""
    width = max(MIN_VISIBLE_BOX, float(box["width"]))
    height = max(MIN_VISIBLE_BOX, float(box["height"]))
    size = min(width, height, 1.0)
    cx = float(box["x"]) + width / 2.0
    cy = float(box["y"]) + height / 2.0
    x = min(max(0.0, cx - size / 2.0), 1.0 - size)
    y = min(max(0.0, cy - size / 2.0), 1.0 - size)
    return {"x": x, "y": y, "width": size, "height": size}


def zoom_box_in_matte(
    cx: float,
    cy: float,
    scale: float,
    matte: dict[str, float],
) -> dict[str, float]:
    """Viewport inside ``matte`` with the same aspect, sized by ``scale``."""
    mw = max(MIN_VISIBLE_BOX, float(matte["width"]))
    mh = max(MIN_VISIBLE_BOX, float(matte["height"]))
    mx = float(matte["x"])
    my = float(matte["y"])
    factor = 1.0 / max(1.01, float(scale))
    vw = max(MIN_VISIBLE_BOX, min(mw, mw * factor))
    vh = max(MIN_VISIBLE_BOX, min(mh, mh * factor))
    x = min(max(mx, float(cx) - vw / 2.0), mx + mw - vw)
    y = min(max(my, float(cy) - vh / 2.0), my + mh - vh)
    return {"x": x, "y": y, "width": vw, "height": vh}


def lock_box_to_matte_aspect(
    box: dict[str, float],
    matte: dict[str, float],
) -> dict[str, float]:
    """Force box aspect to match ``matte`` and keep the box inside the matte."""
    mw = max(MIN_VISIBLE_BOX, float(matte["width"]))
    mh = max(MIN_VISIBLE_BOX, float(matte["height"]))
    mx = float(matte["x"])
    my = float(matte["y"])
    aspect = mw / mh
    width = max(MIN_VISIBLE_BOX, float(box["width"]))
    height = max(MIN_VISIBLE_BOX, float(box["height"]))
    cx = float(box["x"]) + width / 2.0
    cy = float(box["y"]) + height / 2.0
    # Fit the largest matte-aspect rect that fits inside the requested size and matte.
    if width / height > aspect:
        vh = min(height, mh)
        vw = min(vh * aspect, mw)
        vh = vw / aspect
    else:
        vw = min(width, mw)
        vh = min(vw / aspect, mh)
        vw = vh * aspect
    vw = max(MIN_VISIBLE_BOX, min(mw, vw))
    vh = max(MIN_VISIBLE_BOX, min(mh, vh))
    x = min(max(mx, cx - vw / 2.0), mx + mw - vw)
    y = min(max(my, cy - vh / 2.0), my + mh - vh)
    return {"x": x, "y": y, "width": vw, "height": vh}


def clip_box_to_frame(box: dict[str, float]) -> dict[str, float] | None:
    """Visible intersection with the unit square, or None if the box is fully off-frame."""
    x0 = float(box["x"])
    y0 = float(box["y"])
    x1 = x0 + float(box["width"])
    y1 = y0 + float(box["height"])
    vis_x0 = max(0.0, x0)
    vis_y0 = max(0.0, y0)
    vis_x1 = min(1.0, x1)
    vis_y1 = min(1.0, y1)
    width = vis_x1 - vis_x0
    height = vis_y1 - vis_y0
    if width <= 1e-6 or height <= 1e-6:
        return None
    return {"x": vis_x0, "y": vis_y0, "width": width, "height": height}


def keep_box_visible(
    box: dict[str, float],
    *,
    min_visible: float = MIN_VISIBLE_BOX,
) -> dict[str, float]:
    """Shift a possibly overflowing box so a handle-sized sliver stays on-frame."""
    width = max(min_visible, min(_MAX_BOX_SPAN, float(box["width"])))
    height = max(min_visible, min(_MAX_BOX_SPAN, float(box["height"])))
    x = min(1.0 - min_visible, max(min_visible - width, float(box["x"])))
    y = min(1.0 - min_visible, max(min_visible - height, float(box["y"])))
    return {"x": x, "y": y, "width": width, "height": height}


def box_allows_overflow(op_type: OperationType) -> bool:
    return op_type in _BOX_TYPES


def boxes_close(a: dict[str, float], b: dict[str, float], *, eps: float = BOX_EPS) -> bool:
    return all(abs(float(a[key]) - float(b[key])) <= eps for key in ("x", "y", "width", "height"))


def clamp_offset(offset: float) -> float:
    return max(0.0, min(1.0, float(offset)))


def normalize_box(raw: Any, *, overflow: bool = False) -> dict[str, float] | None:
    if not isinstance(raw, dict):
        return None
    try:
        x = float(raw.get("x", 0))
        y = float(raw.get("y", 0))
        width = float(raw.get("width", 0))
        height = float(raw.get("height", 0))
    except (TypeError, ValueError):
        return None
    if width <= 0.001 or height <= 0.001:
        return None
    if overflow:
        return keep_box_visible({"x": x, "y": y, "width": width, "height": height})
    x = _clamp01(x)
    y = _clamp01(y)
    width = min(width, 1.0 - x)
    height = min(height, 1.0 - y)
    if width <= 0.001 or height <= 0.001:
        return None
    return {"x": x, "y": y, "width": width, "height": height}


def interpolate_boxes(start: dict[str, float], end: dict[str, float], t: float) -> dict[str, float]:
    t = max(0.0, min(1.0, float(t)))
    return {
        "x": float(start["x"]) + (float(end["x"]) - float(start["x"])) * t,
        "y": float(start["y"]) + (float(end["y"]) - float(start["y"])) * t,
        "width": float(start["width"]) + (float(end["width"]) - float(start["width"])) * t,
        "height": float(start["height"]) + (float(end["height"]) - float(start["height"])) * t,
    }


def sample_box_at_seconds(keyframes: Sequence[SpatialKeyframe], seconds: float) -> dict[str, float]:
    kfs = _sorted_unique(keyframes)
    if not kfs:
        return dict(DEFAULT_BOX)
    seconds = float(seconds)
    if seconds <= kfs[0].seconds:
        return copy_box(kfs[0].box)
    for index in range(1, len(kfs)):
        prev, nxt = kfs[index - 1], kfs[index]
        if seconds <= nxt.seconds:
            span = nxt.seconds - prev.seconds
            t = 0.0 if span <= TIME_EPS else (seconds - prev.seconds) / span
            return interpolate_boxes(prev.box, nxt.box, t)
    return copy_box(kfs[-1].box)


def _span_at_seconds(
    keyframes: Sequence[SpatialKeyframe],
    seconds: float,
) -> tuple[SpatialKeyframe, SpatialKeyframe, float] | None:
    """Pose pair containing *seconds*, and the 0–1 blend across that pair."""
    kfs = _sorted_unique(keyframes)
    if len(kfs) < 2:
        return None
    seconds = float(seconds)
    if seconds <= kfs[0].seconds or seconds >= kfs[-1].seconds:
        return None
    for index in range(1, len(kfs)):
        prev, nxt = kfs[index - 1], kfs[index]
        if seconds <= nxt.seconds:
            span = nxt.seconds - prev.seconds
            blend = 0.0 if span <= TIME_EPS else (seconds - prev.seconds) / span
            return prev, nxt, max(0.0, min(1.0, blend))
    return None


def preview_box_at_seconds(
    keyframes: Sequence[SpatialKeyframe],
    seconds: float,
) -> dict[str, float]:
    """Outline pose matching preview blur: size holds the larger box, center moves.

    A time-varying crop size reinitializes lavfi every frame, so the preview
    uses ``max(width)`` and ``max(height)`` for the whole span and slides the
    center. The outline uses that same box.
    """
    span = _span_at_seconds(keyframes, seconds)
    if span is None:
        return sample_box_at_seconds(keyframes, seconds)
    prev, nxt, blend = span
    width = max(float(prev.box["width"]), float(nxt.box["width"]))
    height = max(float(prev.box["height"]), float(nxt.box["height"]))
    cx0 = float(prev.box["x"]) + float(prev.box["width"]) / 2.0
    cy0 = float(prev.box["y"]) + float(prev.box["height"]) / 2.0
    cx1 = float(nxt.box["x"]) + float(nxt.box["width"]) / 2.0
    cy1 = float(nxt.box["y"]) + float(nxt.box["height"]) / 2.0
    cx = cx0 + (cx1 - cx0) * blend
    cy = cy0 + (cy1 - cy0) * blend
    x = min(max(0.0, cx - width / 2.0), max(0.0, 1.0 - width))
    y = min(max(0.0, cy - height / 2.0), max(0.0, 1.0 - height))
    return {"x": x, "y": y, "width": width, "height": height}


def neighbor_pose_boxes(
    keyframes: Sequence[SpatialKeyframe],
    seconds: float,
    *,
    snap: float = 0.05,
) -> list[dict[str, float]]:
    """Authored boxes for the previous and next pose around *seconds*.

    A pose the playhead is sitting on is the live outline, so it is left out.
    """
    kfs = _sorted_unique(keyframes)
    if len(kfs) < 2:
        return []
    seconds = float(seconds)
    nearest = min(range(len(kfs)), key=lambda index: abs(kfs[index].seconds - seconds))
    if abs(kfs[nearest].seconds - seconds) <= snap:
        indexes = [nearest - 1, nearest + 1]
    elif seconds <= kfs[0].seconds:
        indexes = [1]
    elif seconds >= kfs[-1].seconds:
        indexes = [len(kfs) - 2]
    else:
        indexes = []
        for index in range(1, len(kfs)):
            if seconds <= kfs[index].seconds:
                indexes = [index - 1, index]
                break
    return [copy_box(kfs[index].box) for index in indexes if 0 <= index < len(kfs)]


def sample_box_at_offset(keyframes: Sequence[SpatialKeyframe], offset: float) -> dict[str, float]:
    """Backward-compatible alias: *offset* is treated as absolute seconds."""
    return sample_box_at_seconds(keyframes, offset)


def spatial_keyframes(op: Operation) -> list[SpatialKeyframe]:
    """Authored poses at absolute times; never invents extra start/end holds."""
    if not is_spatial_operation(op.type):
        return []
    start_s = float(op.time_range.start.seconds)
    end_s = float(op.time_range.end.seconds)
    parsed = _keyframes_from_params(op, start_s, end_s)
    if not parsed:
        start = _start_box(op)
        if start is None:
            return []
        parsed = [SpatialKeyframe(start_s, start)]
        end = _end_box(op, start)
        if end is not None:
            parsed.append(SpatialKeyframe(end_s, end))
    return _sorted_unique(parsed)


def has_spatial_animation(op: Operation) -> bool:
    return len(spatial_keyframes(op)) >= 2


def set_spatial_keyframes(op: Operation, keyframes: Sequence[SpatialKeyframe]) -> None:
    """Write box / box_end / box_keyframes using absolute seconds."""
    kfs = _sorted_unique(keyframes)
    if not kfs:
        return
    params = op.params
    first = copy_box(kfs[0].box)
    params["box"] = first
    params.pop("box_end", None)
    params.pop("box_keyframes", None)
    params.pop("x_end", None)
    params.pop("y_end", None)
    if op.type in _ZOOM_TYPES:
        params["scale"] = _scale_from_box(first, params.get("scale"))
        params.pop("x", None)
        params.pop("y", None)
    if len(kfs) == 1:
        return
    params["box_end"] = copy_box(kfs[-1].box)
    start_s = float(op.time_range.start.seconds)
    end_s = float(op.time_range.end.seconds)
    endpoint_pair = (
        len(kfs) == 2
        and abs(kfs[0].seconds - start_s) <= TIME_EPS
        and abs(kfs[-1].seconds - end_s) <= TIME_EPS
    )
    if not endpoint_pair:
        params["box_keyframes"] = [
            {"seconds": kf.seconds, "box": copy_box(kf.box)} for kf in kfs
        ]


def add_spatial_keyframe(
    op: Operation,
    seconds: float,
    box: dict[str, float] | None = None,
) -> int:
    """Insert exactly one pose at *seconds*, or select the pose already there."""
    kfs = list(spatial_keyframes(op))
    seconds = max(0.0, float(seconds))
    if not kfs:
        kfs = [SpatialKeyframe(seconds, dict(DEFAULT_BOX))]
    for index, kf in enumerate(kfs):
        if abs(kf.seconds - seconds) <= TIME_EPS:
            if box is not None:
                kfs[index] = SpatialKeyframe(kf.seconds, copy_box(box))
                set_spatial_keyframes(op, kfs)
            return index
    pose = copy_box(box) if box is not None else sample_box_at_seconds(kfs, seconds)
    kfs.append(SpatialKeyframe(seconds, pose))
    set_spatial_keyframes(op, kfs)
    return _index_near_seconds(spatial_keyframes(op), seconds)


def move_spatial_keyframe(op: Operation, index: int, seconds: float) -> int:
    """Retiming a pose. Returns the index after sort/merge."""
    kfs = list(spatial_keyframes(op))
    if not kfs:
        return 0
    index = max(0, min(int(index), len(kfs) - 1))
    seconds = max(0.0, float(seconds))
    kfs[index] = SpatialKeyframe(seconds, copy_box(kfs[index].box))
    set_spatial_keyframes(op, kfs)
    return _index_near_seconds(spatial_keyframes(op), seconds)


def remove_spatial_keyframe(op: Operation, index: int) -> int:
    """Drop a pose. The last remaining pose stays as a static box."""
    kfs = list(spatial_keyframes(op))
    if len(kfs) <= 1:
        return 0
    index = max(0, min(int(index), len(kfs) - 1))
    del kfs[index]
    set_spatial_keyframes(op, kfs)
    remaining = spatial_keyframes(op)
    return min(index, max(0, len(remaining) - 1))


def keyframe_seconds(op: Operation, index: int) -> float:
    kfs = spatial_keyframes(op)
    if not kfs:
        return float(op.time_range.start.seconds)
    index = max(0, min(index, len(kfs) - 1))
    return float(kfs[index].seconds)


def retarget_spatial_keyframes(
    op: Operation,
    old_start: float,
    old_end: float,
    new_start: float,
    new_end: float,
) -> None:
    """Keys stay on the master timeline. Region changes never retime them."""
    del op, old_start, old_end, new_start, new_end


def expand_spatial_operation(op: Operation) -> list[Operation]:
    """One authored op → one or more two-pose segments (corr.json / preview)."""
    if not is_spatial_operation(op.type):
        return [op]
    kfs = _keyframes_for_export(op, spatial_keyframes(op))
    if len(kfs) <= 1:
        return [op]
    if len(kfs) == 2:
        params = dict(op.params)
        params.pop("box_keyframes", None)
        params["box"] = copy_box(kfs[0].box)
        params["box_end"] = copy_box(kfs[1].box)
        return [replace(op, params=params)]
    segments: list[Operation] = []
    last_index = len(kfs) - 2
    for index in range(len(kfs) - 1):
        a, b = kfs[index], kfs[index + 1]
        seg_start = a.seconds
        seg_end = b.seconds
        if index == 0:
            seg_start = min(seg_start, float(op.time_range.start.seconds))
        if index == last_index:
            seg_end = max(seg_end, float(op.time_range.end.seconds))
        if seg_end <= seg_start + TIME_EPS / 10:
            continue
        params = dict(op.params)
        params.pop("box_keyframes", None)
        params.pop("x_end", None)
        params.pop("y_end", None)
        params["box"] = copy_box(a.box)
        params["box_end"] = copy_box(b.box)
        segments.append(
            replace(
                op,
                id=op.id if index == 0 else f"{op.id}__k{index}",
                time_range=TimeRange(
                    start=_seconds_to_timepoint(seg_start),
                    end=_seconds_to_timepoint(seg_end),
                ),
                params=params,
            )
        )
    return segments or [op]


def merge_abutting_spatial_operations(operations: list[Operation]) -> list[Operation]:
    """Fold consecutive abutting same-action spatial edits into keyframed ops."""
    if len(operations) < 2:
        return list(operations)
    merged: list[Operation] = []
    current = operations[0]
    for nxt in operations[1:]:
        combined = _try_merge_pair(current, nxt)
        if combined is None:
            merged.append(current)
            current = nxt
        else:
            current = combined
    merged.append(current)
    return merged


def _keyframes_from_params(
    op: Operation,
    start_s: float,
    end_s: float,
) -> list[SpatialKeyframe]:
    raw = op.params.get("box_keyframes")
    if not isinstance(raw, list) or not raw:
        return []
    duration = max(end_s - start_s, 1e-9)
    parsed: list[SpatialKeyframe] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        box_raw = item.get("box") if isinstance(item.get("box"), dict) else item
        box = normalize_box(box_raw, overflow=box_allows_overflow(op.type))
        if box is None:
            continue
        seconds = _item_seconds(item, start_s, duration)
        if seconds is None:
            continue
        parsed.append(SpatialKeyframe(seconds, box))
    return parsed


def _item_seconds(item: dict[str, Any], start_s: float, duration: float) -> float | None:
    if "seconds" in item:
        try:
            return float(item["seconds"])
        except (TypeError, ValueError):
            return None
    if "offset" in item:
        try:
            return start_s + clamp_offset(float(item["offset"])) * duration
        except (TypeError, ValueError):
            return None
    return None


def _start_box(op: Operation) -> dict[str, float] | None:
    params = op.params
    box = normalize_box(params.get("box"), overflow=box_allows_overflow(op.type))
    if box is not None:
        return box
    if op.type in _ZOOM_TYPES:
        scale = float(params.get("scale", DEFAULT_ZOOM_SCALE))
        return box_from_center_scale(
            float(params.get("x", 0.5)),
            float(params.get("y", 0.5)),
            scale,
        )
    if op.type in _BOX_TYPES:
        return dict(DEFAULT_BOX)
    return None


def _end_box(op: Operation, start: dict[str, float]) -> dict[str, float] | None:
    params = op.params
    end_raw = params.get("box_end")
    if isinstance(end_raw, dict):
        end = {
            "x": float(end_raw.get("x", start["x"])),
            "y": float(end_raw.get("y", start["y"])),
            "width": float(end_raw.get("width", start["width"])),
            "height": float(end_raw.get("height", start["height"])),
        }
        normalized = normalize_box(end, overflow=box_allows_overflow(op.type))
        return end if normalized is None else normalized
    if op.type in _ZOOM_TYPES and "x_end" in params:
        scale = float(params.get("scale", DEFAULT_ZOOM_SCALE))
        return box_from_center_scale(
            float(params.get("x_end", params.get("x", 0.5))),
            float(params.get("y_end", params.get("y", 0.5))),
            scale,
        )
    return None


def _keyframes_for_export(
    op: Operation,
    keyframes: Sequence[SpatialKeyframe],
) -> list[SpatialKeyframe]:
    """Playback poses inside the enable window.

    Authored keys stay on source time and may sit outside the region. Export
    samples the pose at in/out and keeps only interior keys so segments do
    not extend past the edit.
    """
    kfs = _sorted_unique(keyframes)
    if len(kfs) < 2:
        return kfs
    start_s = float(op.time_range.start.seconds)
    end_s = float(op.time_range.end.seconds)
    out = [SpatialKeyframe(start_s, sample_box_at_seconds(kfs, start_s))]
    for kf in kfs:
        if start_s + TIME_EPS < kf.seconds < end_s - TIME_EPS:
            out.append(SpatialKeyframe(kf.seconds, copy_box(kf.box)))
    out.append(SpatialKeyframe(end_s, sample_box_at_seconds(kfs, end_s)))
    return _sorted_unique(out)


def _drop_identical_edge_holds(
    keyframes: Sequence[SpatialKeyframe],
    start_s: float,
    end_s: float,
) -> list[SpatialKeyframe]:
    """Strip synthesized edge holds after merging exported segments."""
    kfs = _sorted_unique(keyframes)
    if len(kfs) >= 3 and abs(kfs[-1].seconds - end_s) <= TIME_EPS and boxes_close(
        kfs[-2].box, kfs[-1].box
    ):
        kfs = kfs[:-1]
    if len(kfs) >= 3 and abs(kfs[0].seconds - start_s) <= TIME_EPS and boxes_close(
        kfs[0].box, kfs[1].box
    ):
        kfs = kfs[1:]
    return kfs


def _index_near_seconds(keyframes: Sequence[SpatialKeyframe], seconds: float) -> int:
    if not keyframes:
        return 0
    return min(range(len(keyframes)), key=lambda i: abs(keyframes[i].seconds - seconds))


def _sorted_unique(keyframes: Sequence[SpatialKeyframe]) -> list[SpatialKeyframe]:
    ordered = sorted(
        (SpatialKeyframe(float(kf.seconds), copy_box(kf.box)) for kf in keyframes),
        key=lambda kf: kf.seconds,
    )
    unique: list[SpatialKeyframe] = []
    for kf in ordered:
        if unique and abs(unique[-1].seconds - kf.seconds) <= TIME_EPS:
            unique[-1] = kf
        else:
            unique.append(kf)
    return unique


def _scale_from_box(box: dict[str, float], existing: Any) -> float:
    span = max(float(box["width"]), float(box["height"]), 0.02)
    scale = 1.0 / span
    if existing is not None:
        try:
            current = float(existing)
        except (TypeError, ValueError):
            current = scale
        if abs(current - scale) < 0.05:
            return max(1.01, min(10.0, current))
    return max(1.01, min(10.0, scale))


def _seconds_to_timepoint(seconds: float) -> TimePoint:
    return TimePoint(Fraction(seconds).limit_denominator(100_000))


def _clamp01(value: Any) -> float:
    return max(0.0, min(1.0, float(value)))


def _effect_signature(op: Operation) -> tuple[Any, ...]:
    params = op.params
    return (
        op.type,
        tuple(op.categories),
        op.description,
        op.severity,
        round(float(params.get("radius", 0.0)), 6),
        round(float(params.get("size", 0.0)), 6),
        round(float(params.get("scale", 0.0)), 6),
    )


def _try_merge_pair(left: Operation, right: Operation) -> Operation | None:
    if not is_spatial_operation(left.type) or left.type != right.type:
        return None
    if not left.enabled or not right.enabled:
        return None
    if _effect_signature(left) != _effect_signature(right):
        return None
    if abs(float(left.time_range.end.seconds) - float(right.time_range.start.seconds)) > TIME_EPS:
        return None
    left_kfs = spatial_keyframes(left)
    right_kfs = spatial_keyframes(right)
    if not left_kfs or not right_kfs:
        return None
    if not boxes_close(left_kfs[-1].box, right_kfs[0].box):
        return None
    t0 = float(left.time_range.start.seconds)
    t1 = float(right.time_range.end.seconds)
    if t1 - t0 <= TIME_EPS:
        return None
    combined: list[SpatialKeyframe] = []
    for kfs in (left_kfs, right_kfs):
        for kf in kfs:
            if combined and abs(combined[-1].seconds - kf.seconds) <= TIME_EPS:
                continue
            combined.append(SpatialKeyframe(kf.seconds, copy_box(kf.box)))
    result = replace(
        left,
        time_range=TimeRange(start=left.time_range.start, end=right.time_range.end),
        params=deepcopy(left.params),
    )
    set_spatial_keyframes(result, combined)
    collapsed = _drop_identical_edge_holds(spatial_keyframes(result), t0, t1)
    if (
        len(collapsed) >= 2
        and all(boxes_close(collapsed[0].box, kf.box) for kf in collapsed)
        and (
            len(collapsed) > 2
            or (
                abs(collapsed[0].seconds - t0) <= TIME_EPS
                and abs(collapsed[-1].seconds - t1) <= TIME_EPS
            )
        )
    ):
        set_spatial_keyframes(result, [collapsed[0]])
    else:
        set_spatial_keyframes(result, collapsed)
    return result
