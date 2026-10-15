"""Compile video effects for lavfi preview (CorrMedia-compatible)."""

from __future__ import annotations

from dataclasses import dataclass

from corrcut.domain.operation import (
    DEFAULT_BLUR_RADIUS,
    DEFAULT_BOX,
    DEFAULT_PIXEL_SIZE,
    DEFAULT_ZOOM_SCALE,
    Operation,
    OperationType,
)
from corrcut.domain.spatial_keyframes import (
    clip_box_to_frame,
    expand_spatial_operation,
    lock_box_to_source_aspect,
)
from corrcut.domain.video_matte import is_plausible_bar_matte, is_trivial_matte

_PREVIEW_VIDEO_TYPES = frozenset(
    {
        OperationType.BLANK,
        OperationType.COVER,
        OperationType.BLUR,
        OperationType.PIXELATE,
        OperationType.ZOOM,
        OperationType.CROP,
    }
)

# Regional effects: crop a box, transform it, overlay back at the box origin.
_REGIONAL_TYPES = frozenset(
    {
        OperationType.COVER,
        OperationType.BLUR,
        OperationType.PIXELATE,
    }
)

# Full-frame replacements (punch-in / blackout / crop-to-window).
_FULL_FRAME_TYPES = frozenset(
    {
        OperationType.BLANK,
        OperationType.ZOOM,
        OperationType.CROP,
    }
)


@dataclass(frozen=True)
class VideoCompileOptions:
    """Optional framing context for video effect compilation."""

    preserve_letterbox: bool = False
    matte: dict[str, float] | None = None
    frame_width: int = 0
    frame_height: int = 0

    def active_matte(self) -> dict[str, float] | None:
        if not self.preserve_letterbox or self.matte is None:
            return None
        if is_trivial_matte(self.matte):
            return None
        if not is_plausible_bar_matte(self.matte):
            return None
        return self.matte


def _f(value: float) -> str:
    return format(float(value), ".15g")


def _enable(start: float, end: float) -> str:
    return f"between(t,{_f(start)},{_f(end)})"


def _unit(start: float, end: float, from_v: float, to_v: float) -> str:
    """Linear 0–1 unit over [start,end] (FFmpeg expr), matching CorrMedia."""
    if abs(to_v - from_v) < 0.0005:
        return _f(from_v)
    span = end - start
    if span <= 0:
        return _f(from_v)
    return (
        f"({_f(from_v)}+({_f(to_v - from_v)})*clip((t-{_f(start)})/{_f(span)},0,1))"
    )


def _norm_to_pixel_span(size_var: str, origin_unit: str, length_unit: str) -> tuple[str, str]:
    """Integer origin + length that cover normalized [origin, origin+length].

    Crop/overlay truncate each float separately. ``floor(x)+trunc(w)`` then
    falls short of ``ceil(x+w)``, which shows up as a 1–2px gap on the right
    (and bottom) when the box is flush to the picture edge.
    """
    origin = f"floor({size_var}*max(0,({origin_unit})))"
    end = f"ceil({size_var}*min(1,({origin_unit})+({length_unit})))"
    length = f"max(2,{end}-{origin})"
    return origin, length


def _box_from_center_scale(cx: float, cy: float, scale: float) -> dict[str, float]:
    width = min(1.0, 1.0 / max(1.01, scale))
    height = width
    return {
        "x": max(0.0, min(1.0 - width, cx - width / 2)),
        "y": max(0.0, min(1.0 - height, cy - height / 2)),
        "width": width,
        "height": height,
    }


def _box_xywh(
    op: Operation,
    *,
    opts: VideoCompileOptions | None = None,
) -> tuple[dict[str, float], dict[str, float]] | None:
    """Start/end normalized boxes for spatial video ops; None = full frame."""
    del opts  # Zoom preview keeps square boxes; matte is applied via drawbox bars.
    if op.type == OperationType.BLANK:
        return None

    box = op.params.get("box")
    if isinstance(box, dict):
        start = {
            "x": float(box.get("x", 0.35)),
            "y": float(box.get("y", 0.35)),
            "width": float(box.get("width", 0.3)),
            "height": float(box.get("height", 0.3)),
        }
    elif op.type in {OperationType.ZOOM, OperationType.CROP}:
        scale = float(op.params.get("scale", DEFAULT_ZOOM_SCALE))
        start = _box_from_center_scale(
            float(op.params.get("x", 0.5)),
            float(op.params.get("y", 0.5)),
            scale,
        )
    elif op.type in _REGIONAL_TYPES:
        start = {k: float(v) for k, v in DEFAULT_BOX.items()}
    else:
        return None

    end_raw = op.params.get("box_end")
    if isinstance(end_raw, dict):
        end = {
            "x": float(end_raw.get("x", start["x"])),
            "y": float(end_raw.get("y", start["y"])),
            "width": float(end_raw.get("width", start["width"])),
            "height": float(end_raw.get("height", start["height"])),
        }
    elif op.type in {OperationType.ZOOM, OperationType.CROP} and "x_end" in op.params:
        scale = float(op.params.get("scale", DEFAULT_ZOOM_SCALE))
        end = _box_from_center_scale(
            float(op.params.get("x_end", op.params.get("x", 0.5))),
            float(op.params.get("y_end", op.params.get("y", 0.5))),
            scale,
        )
    else:
        end = dict(start)
    if op.type == OperationType.ZOOM:
        start = lock_box_to_source_aspect(start)
        end = lock_box_to_source_aspect(end)
    return start, end


@dataclass(frozen=True)
class CompiledVideoFiltergraph:
    filter_complex: str
    output_video_label: str = "vout"


def compile_preview_video_effects(
    operations: list[Operation],
    *,
    preserve_letterbox: bool = False,
    matte: dict[str, float] | None = None,
    frame_width: int = 0,
    frame_height: int = 0,
    time_window: tuple[float, float] | None = None,
) -> CompiledVideoFiltergraph | None:
    """
    Build an FFmpeg video filter_complex for video censor ops (original timeline).

    Uses CorrMedia's split → effect → timed overlay pattern. Input pad is [0:v];
    output pad is [vout]. Returns None when there are no previewable video ops.

    When ``preserve_letterbox`` is True and ``matte`` is a non-trivial content
    rect, Zoom uses the normal full-frame punch-in then paints black bars back
    with constant-pixel ``drawbox`` (expression drawbox after scale breaks libmpv).

    ``time_window`` (start_s, end_s) keeps only segments that overlap that range.
    Preview uses a playhead-centered window so distant blur/pixelate chains are
    not evaluated every frame (their crop/scale stages are not enable-gated).
    """
    opts = VideoCompileOptions(
        preserve_letterbox=preserve_letterbox,
        matte=matte,
        frame_width=max(0, int(frame_width)),
        frame_height=max(0, int(frame_height)),
    )
    effects: list[Operation] = []
    for op in operations:
        if not op.enabled or op.type not in _PREVIEW_VIDEO_TYPES:
            continue
        effects.extend(expand_spatial_operation(op))
    effects.sort(key=lambda o: (o.order, o.time_range.start.seconds))
    if not effects:
        return None

    win_start: float | None = None
    win_end: float | None = None
    if time_window is not None:
        win_start = float(time_window[0])
        win_end = float(time_window[1])
        if win_end < win_start:
            win_start, win_end = win_end, win_start

    parts: list[str] = []
    current = "0:v"

    for index, op in enumerate(effects):
        start = float(op.time_range.start.seconds)
        end = float(op.time_range.end.seconds)
        if end <= start:
            continue
        if win_start is not None and win_end is not None:
            if end <= win_start or start >= win_end:
                continue
        if op.type in _REGIONAL_TYPES and _animated_box_crop_units(op, start, end, opts=opts) is None:
            continue
        base_pad = f"vx{index}b"
        src_pad = f"vx{index}s"
        fx_pad = f"vx{index}f"
        out_pad = f"vx{index}"
        enable = _enable(start, end)

        parts.append(f"[{current}]split=2[{base_pad}][{src_pad}];")
        parts.append(_build_effect_filters(op, src_pad, fx_pad, start, end, opts=opts))
        parts.append(_build_overlay(op, base_pad, fx_pad, out_pad, enable, start, end, opts=opts))
        current = out_pad

    if current == "0:v":
        return None
    if current != "vout":
        parts.append(f"[{current}]null[vout];")
    return CompiledVideoFiltergraph(filter_complex="".join(parts), output_video_label="vout")


def _animated_box_crop_units(
    op: Operation,
    start: float,
    end: float,
    *,
    opts: VideoCompileOptions | None = None,
) -> tuple[str, str, str, str] | None:
    """Return (x, y, w, h) unit expressions for preview crop/overlay.

    Width/height are always numeric constants (never time-varying). Animated
    ``crop`` size reinits lavfi every frame and freezes libmpv.
    """
    boxes = _box_xywh(op, opts=opts)
    if boxes is None:
        return None
    box, end_box = boxes
    if op.type in _REGIONAL_TYPES:
        clipped_start = clip_box_to_frame(box)
        clipped_end = clip_box_to_frame(end_box)
        if clipped_start is None and clipped_end is None:
            return None
        box = clipped_start or clipped_end
        end_box = clipped_end or clipped_start
    w = max(float(box["width"]), float(end_box["width"]))
    h = max(float(box["height"]), float(end_box["height"]))
    cx0 = float(box["x"]) + float(box["width"]) / 2.0
    cy0 = float(box["y"]) + float(box["height"]) / 2.0
    cx1 = float(end_box["x"]) + float(end_box["width"]) / 2.0
    cy1 = float(end_box["y"]) + float(end_box["height"]) / 2.0
    cx_u = _unit(start, end, cx0, cx1)
    cy_u = _unit(start, end, cy0, cy1)
    x_u = f"clip((({cx_u})-{_f(w / 2.0)}),0,{_f(max(0.0, 1.0 - w))})"
    y_u = f"clip((({cy_u})-{_f(h / 2.0)}),0,{_f(max(0.0, 1.0 - h))})"
    return x_u, y_u, _f(w), _f(h)


def _crop_filter(
    op: Operation,
    start: float,
    end: float,
    *,
    opts: VideoCompileOptions | None = None,
) -> str | None:
    """Crop expression for a regional/zoom box, or None for full-frame blank."""
    units = _animated_box_crop_units(op, start, end, opts=opts)
    if units is None:
        return None
    x_u, y_u, w_u, h_u = units
    # Width/height must be pure constants (no ``t``). Embedding an animated
    # origin into w via floor(x+w)-floor(x) still references t and freezes lavfi.
    w_px = f"max(2,ceil(iw*({w_u})))"
    h_px = f"max(2,ceil(ih*({h_u})))"
    x_px = f"floor(iw*max(0,({x_u})))"
    y_px = f"floor(ih*max(0,({y_u})))"
    return (
        f"crop=w='{w_px}':h='{h_px}':"
        f"x='min(iw-ow,max(0,{x_px}))':y='min(ih-oh,max(0,{y_px}))':"
        f"exact=1"
    )


def _build_effect_filters(
    op: Operation,
    src_pad: str,
    fx_pad: str,
    start: float,
    end: float,
    *,
    opts: VideoCompileOptions | None = None,
) -> str:
    """Build the effect pad (CorrMedia: crop region → transform → timed overlay)."""
    compile_opts = opts or VideoCompileOptions()
    crop = _crop_filter(op, start, end, opts=compile_opts)

    if op.type == OperationType.BLANK:
        return f"[{src_pad}]drawbox=0:0:iw:ih:black:t=fill[{fx_pad}];"

    if op.type == OperationType.COVER:
        fill = "drawbox=0:0:iw:ih:black:t=fill"
        if crop is None:
            return f"[{src_pad}]null[{fx_pad}];"
        return f"[{src_pad}]{crop},{fill}[{fx_pad}];"

    if op.type == OperationType.BLUR:
        # Soft blur via downscale/upscale (bilinear). Avoid boxblur/gblur: boxblur
        # rejects large radii on small crops (greys lavfi), and gblur has crashed
        # libmpv on Windows in this app.
        radius = max(2.0, min(64.0, float(op.params.get("radius", DEFAULT_BLUR_RADIUS))))
        effect = (
            f"scale='max(2,floor(iw/{_f(radius)}))':'max(2,floor(ih/{_f(radius)}))',"
            f"scale='iw*{_f(radius)}':'ih*{_f(radius)}'"
        )
        if crop is None:
            return f"[{src_pad}]{effect}[{fx_pad}];"
        return f"[{src_pad}]{crop},{effect}[{fx_pad}];"

    if op.type == OperationType.PIXELATE:
        size = max(2.0, float(op.params.get("size", DEFAULT_PIXEL_SIZE)))
        # Downscale then nearest-neighbor upscale → mosaic blocks of ~size px.
        # After the first scale, iw/ih are already reduced; iw*size restores.
        effect = (
            f"scale='max(2,floor(iw/{_f(size)}))':'max(2,floor(ih/{_f(size)}))',"
            f"scale='iw*{_f(size)}':'ih*{_f(size)}':flags=neighbor"
        )
        if crop is None:
            return f"[{src_pad}]{effect}[{fx_pad}];"
        return f"[{src_pad}]{crop},{effect}[{fx_pad}];"

    if op.type == OperationType.ZOOM:
        # Punch-in: crop the viewport, then scale back to the source frame size.
        units = _animated_box_crop_units(op, start, end, opts=compile_opts)
        if units is None or crop is None:
            return f"[{src_pad}]null[{fx_pad}];"
        _x_u, _y_u, w_u, h_u = units
        # After crop, iw/ih are the crop size; divide by normalized box size → full frame.
        scale_up = f"scale='max(2,iw/max(0.01,({w_u})))':'max(2,ih/max(0.01,({h_u})))'"
        bars = _letterbox_bar_filters(
            compile_opts.active_matte(),
            frame_width=compile_opts.frame_width,
            frame_height=compile_opts.frame_height,
        )
        if bars:
            return f"[{src_pad}]{crop},{scale_up},{bars}[{fx_pad}];"
        return f"[{src_pad}]{crop},{scale_up}[{fx_pad}];"

    if op.type == OperationType.CROP:
        # Keep the box at native scale; black out everything outside (no magnification).
        # Build: black full-frame from src, then overlay the cropped window.
        if crop is None:
            return f"[{src_pad}]null[{fx_pad}];"
        # Label pads local to this effect step.
        black_pad = f"{fx_pad}k"
        win_pad = f"{fx_pad}w"
        return (
            f"[{src_pad}]split=2[{black_pad}s][{win_pad}s];"
            f"[{black_pad}s]drawbox=0:0:iw:ih:black:t=fill[{black_pad}];"
            f"[{win_pad}s]{crop}[{win_pad}];"
            f"[{black_pad}][{win_pad}]overlay="
            f"x='min(main_w-overlay_w,max(0,floor(main_w*({_crop_overlay_x(op, start, end, opts=compile_opts)}))))':"
            f"y='min(main_h-overlay_h,max(0,floor(main_h*({_crop_overlay_y(op, start, end, opts=compile_opts)}))))'"
            f"[{fx_pad}];"
        )

    return f"[{src_pad}]null[{fx_pad}];"


def _letterbox_bar_filters(
    matte: dict[str, float] | None,
    *,
    frame_width: int = 0,
    frame_height: int = 0,
) -> str:
    """Paint letterbox/pillarbox bars with constant pixel drawbox (mpv-safe).

    Expression-based drawbox after ``scale`` collapses lavfi output to a tiny
    corner tile under libmpv. Positional ``drawbox=0:0:iw:N:black:t=fill`` matches
    the form already used by blank/cover.
    """
    if matte is None or frame_width < 2 or frame_height < 2:
        return ""
    mx = max(0.0, min(1.0, float(matte["x"])))
    my = max(0.0, min(1.0, float(matte["y"])))
    mw = max(0.0, min(1.0 - mx, float(matte["width"])))
    mh = max(0.0, min(1.0 - my, float(matte["height"])))
    parts: list[str] = []
    top_px = int(frame_height * my)
    if top_px >= 2:
        parts.append(f"drawbox=0:0:iw:{top_px}:black:t=fill")
    bottom_y = int(frame_height * (my + mh))
    if bottom_y <= frame_height - 2:
        parts.append(f"drawbox=0:{bottom_y}:iw:ih:black:t=fill")
    left_px = int(frame_width * mx)
    if left_px >= 2:
        parts.append(f"drawbox=0:0:{left_px}:ih:black:t=fill")
    right_x = int(frame_width * (mx + mw))
    if right_x <= frame_width - 2:
        parts.append(f"drawbox={right_x}:0:iw:ih:black:t=fill")
    return ",".join(parts)


def _crop_overlay_x(
    op: Operation,
    start: float,
    end: float,
    *,
    opts: VideoCompileOptions | None = None,
) -> str:
    units = _animated_box_crop_units(op, start, end, opts=opts)
    if units is None:
        return "0"
    return units[0]


def _crop_overlay_y(
    op: Operation,
    start: float,
    end: float,
    *,
    opts: VideoCompileOptions | None = None,
) -> str:
    units = _animated_box_crop_units(op, start, end, opts=opts)
    if units is None:
        return "0"
    return units[1]


def _build_overlay(
    op: Operation,
    base_pad: str,
    fx_pad: str,
    out_pad: str,
    enable: str,
    start: float,
    end: float,
    *,
    opts: VideoCompileOptions | None = None,
) -> str:
    compile_opts = opts or VideoCompileOptions()
    if op.type in _FULL_FRAME_TYPES:
        return f"[{base_pad}][{fx_pad}]overlay=0:0:enable='{enable}'[{out_pad}];"

    units = _animated_box_crop_units(op, start, end, opts=compile_opts)
    if units is not None and op.type in _REGIONAL_TYPES:
        x_u, y_u, _w_u, _h_u = units
        return (
            f"[{base_pad}][{fx_pad}]overlay="
            f"x='min(main_w-overlay_w,max(0,floor(main_w*({x_u}))))':"
            f"y='min(main_h-overlay_h,max(0,floor(main_h*({y_u}))))':"
            f"enable='{enable}'[{out_pad}];"
        )
    return f"[{base_pad}][{fx_pad}]overlay=0:0:enable='{enable}'[{out_pad}];"
