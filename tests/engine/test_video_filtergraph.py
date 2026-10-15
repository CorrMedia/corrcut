"""Tests for video lavfi preview graphs (blank/cover/blur/pixelate/zoom/crop)."""

from __future__ import annotations

from fractions import Fraction
from pathlib import Path

from corrcut.domain.dialogue_target import DialogueTargetStrategy
from corrcut.domain.operation import ITEM_LEVEL_STREAM_INDEX, Operation, OperationType
from corrcut.domain.project import Project, SourceFileRecord, TrackRecord
from corrcut.domain.spatial_keyframes import SpatialKeyframe, set_spatial_keyframes
from corrcut.domain.time_point import TimePoint
from corrcut.engine.video_filtergraph import (
    _box_xywh,
    _norm_to_pixel_span,
    compile_preview_video_effects,
)
from corrcut.preview.lavfi_mpv import (
    MPV_AUDIO_IN,
    MPV_AUDIO_OUT,
    MPV_PASSTHROUGH,
    MPV_VIDEO_IN,
    MPV_VIDEO_OUT,
    compile_playback_lavfi,
    ffmpeg_video_graph_to_mpv_lavfi,
)


def _track() -> TrackRecord:
    return TrackRecord(
        stream_index=1,
        codec="aac",
        channel_layout="stereo",
        channels=2,
        sample_rate=48000,
        language="eng",
        dialogue_target_strategy=DialogueTargetStrategy.FULL_MIX,
    )


def _project(ops: list[Operation]) -> Project:
    return Project(
        source=SourceFileRecord(
            path=Path(__file__),
            fingerprint={},
            probe={"duration_seconds": 10.0},
        ),
        tracks=[_track()],
        selected_audio_stream_index=1,
        operations=ops,
    )


def test_blank_compiles_full_frame_drawbox_overlay() -> None:
    op = Operation.create(
        OperationType.BLANK,
        ITEM_LEVEL_STREAM_INDEX,
        [],
        TimePoint(Fraction(1)),
        TimePoint(Fraction(3)),
    )
    compiled = compile_preview_video_effects([op])
    assert compiled is not None
    graph = compiled.filter_complex
    assert "split=2" in graph
    assert "drawbox=0:0:iw:ih:black:t=fill" in graph
    assert "overlay=0:0:enable='between(t,1,3)'" in graph
    assert graph.endswith("[vout];") or "[vout];" in graph


def test_overflow_cover_crops_visible_intersection() -> None:
    op = Operation.create(
        OperationType.COVER,
        ITEM_LEVEL_STREAM_INDEX,
        [],
        TimePoint(Fraction(2)),
        TimePoint(Fraction(4)),
        params={"box": {"x": -0.25, "y": 0.25, "width": 0.5, "height": 0.5}},
    )
    compiled = compile_preview_video_effects([op])
    assert compiled is not None
    graph = compiled.filter_complex
    assert "crop=w='max(2,ceil(iw*(0.25)))'" in graph
    assert "h='max(2,ceil(ih*(0.5)))'" in graph


def test_cover_compiles_regional_crop_and_overlay() -> None:
    op = Operation.create(
        OperationType.COVER,
        ITEM_LEVEL_STREAM_INDEX,
        [],
        TimePoint(Fraction(2)),
        TimePoint(Fraction(4)),
        params={"box": {"x": 0.1, "y": 0.2, "width": 0.3, "height": 0.4}},
    )
    compiled = compile_preview_video_effects([op])
    assert compiled is not None
    graph = compiled.filter_complex
    assert "exact=1" in graph
    assert "crop=w='max(2,ceil(iw*(0.3)))'" in graph
    assert "crop=w=" in graph and "h='max(2,ceil(ih*(0.4)))'" in graph
    assert "drawbox=0:0:iw:ih:black:t=fill" in graph
    assert "enable='between(t,2,4)'" in graph


def test_norm_to_pixel_span_covers_flush_right_edge() -> None:
    origin, length = _norm_to_pixel_span("iw", "0.7", "0.3")
    assert origin == "floor(iw*max(0,(0.7)))"
    assert "ceil(iw*min(1,(0.7)+(0.3)))" in length


def test_cover_box_end_animates_overlay_position() -> None:
    op = Operation.create(
        OperationType.COVER,
        ITEM_LEVEL_STREAM_INDEX,
        [],
        TimePoint(Fraction(1)),
        TimePoint(Fraction(3)),
        params={
            "box": {"x": 0.1, "y": 0.2, "width": 0.25, "height": 0.25},
            "box_end": {"x": 0.6, "y": 0.5, "width": 0.25, "height": 0.25},
        },
    )
    compiled = compile_preview_video_effects([op])
    assert compiled is not None
    graph = compiled.filter_complex
    assert "clip((t-1)/2,0,1)" in graph
    assert "floor(main_w*" in graph
    assert "floor(main_h*" in graph
    # Size stays constant even while position animates.
    assert "crop=w='max(2,ceil(iw*(0.25)))'" in graph


def test_animated_size_keeps_constant_crop_dimensions() -> None:
    """Time-varying crop w/h freezes libmpv — size must stay constant in preview."""
    op = Operation.create(
        OperationType.BLUR,
        ITEM_LEVEL_STREAM_INDEX,
        [],
        TimePoint(Fraction(1)),
        TimePoint(Fraction(4)),
        params={
            "radius": 8.0,
            "box": {"x": 0.1, "y": 0.1, "width": 0.2, "height": 0.2},
            "box_end": {"x": 0.5, "y": 0.4, "width": 0.45, "height": 0.35},
        },
    )
    compiled = compile_preview_video_effects([op])
    assert compiled is not None
    graph = compiled.filter_complex
    assert "crop=w='max(2,ceil(iw*(0.45)))'" in graph
    assert "h='max(2,ceil(ih*(0.35)))'" in graph
    assert "clip((t-1)/3,0,1)" in graph
    crop_w = graph.split("crop=w='", 1)[1].split("':h='", 1)[0]
    assert "t" not in crop_w
    crop_h = graph.split("':h='", 1)[1].split("':x='", 1)[0]
    assert "t" not in crop_h


def test_keyframed_cover_compiles_abutting_enable_windows() -> None:
    op = Operation.create(
        OperationType.COVER,
        ITEM_LEVEL_STREAM_INDEX,
        [],
        TimePoint(Fraction(1)),
        TimePoint(Fraction(5)),
        params={"box": {"x": 0.1, "y": 0.2, "width": 0.25, "height": 0.25}},
    )
    set_spatial_keyframes(
        op,
        [
            SpatialKeyframe(1.0, {"x": 0.1, "y": 0.2, "width": 0.25, "height": 0.25}),
            SpatialKeyframe(3.0, {"x": 0.4, "y": 0.3, "width": 0.25, "height": 0.25}),
            SpatialKeyframe(5.0, {"x": 0.6, "y": 0.5, "width": 0.25, "height": 0.25}),
        ],
    )
    compiled = compile_preview_video_effects([op])
    assert compiled is not None
    graph = compiled.filter_complex
    assert "enable='between(t,1,3)'" in graph
    assert "enable='between(t,3,5)'" in graph
    assert graph.count("split=2") == 2


def test_ffmpeg_video_graph_to_mpv_relabels() -> None:
    graph = "[0:v]null[vout];"
    out = ffmpeg_video_graph_to_mpv_lavfi(graph)
    assert out.startswith(MPV_VIDEO_IN)
    assert MPV_VIDEO_OUT in out
    assert "[0:v]" not in out
    assert "[vout]" not in out


def test_playback_lavfi_includes_blank_and_audio_passthrough() -> None:
    blank = Operation.create(
        OperationType.BLANK,
        ITEM_LEVEL_STREAM_INDEX,
        [],
        TimePoint(Fraction(1)),
        TimePoint(Fraction(2)),
    )
    project = _project([blank])
    lavfi = compile_playback_lavfi(project, edits_enabled=True)
    assert lavfi is not None
    assert MPV_VIDEO_IN in lavfi
    assert MPV_VIDEO_OUT in lavfi
    assert MPV_AUDIO_IN in lavfi
    assert MPV_AUDIO_OUT in lavfi
    assert "drawbox=0:0:iw:ih:black:t=fill" in lavfi
    assert "anull" in lavfi


def test_playback_lavfi_combines_mute_and_cover() -> None:
    mute = Operation.create(
        OperationType.MUTE,
        1,
        [],
        TimePoint(Fraction(1)),
        TimePoint(Fraction(2)),
    )
    cover = Operation.create(
        OperationType.COVER,
        ITEM_LEVEL_STREAM_INDEX,
        [],
        TimePoint(Fraction(1)),
        TimePoint(Fraction(2)),
    )
    project = _project([mute, cover])
    lavfi = compile_playback_lavfi(project, edits_enabled=True)
    assert lavfi is not None
    assert "drawbox" in lavfi
    assert "volume=" in lavfi
    assert lavfi.count(";") >= 1


def test_blur_compiles_regional_boxblur() -> None:
    op = Operation.create(
        OperationType.BLUR,
        ITEM_LEVEL_STREAM_INDEX,
        [],
        TimePoint(Fraction(1)),
        TimePoint(Fraction(2)),
        params={
            "radius": 12.0,
            "box": {"x": 0.2, "y": 0.3, "width": 0.4, "height": 0.25},
        },
    )
    compiled = compile_preview_video_effects([op])
    assert compiled is not None
    graph = compiled.filter_complex
    assert "exact=1" in graph
    assert "gblur=" not in graph
    assert "boxblur" not in graph
    assert "scale=" in graph
    assert "crop=w='max(2,ceil(iw*(0.4)))'" in graph
    assert "floor(main_w*" in graph
    assert "enable='between(t,1,2)'" in graph


def test_blur_survives_tiny_box_where_boxblur_would_fail() -> None:
    """Soft scale-blur must keep lavfi alive on tiny crops (no boxblur/gblur)."""
    op = Operation.create(
        OperationType.BLUR,
        ITEM_LEVEL_STREAM_INDEX,
        [],
        TimePoint(Fraction(0)),
        TimePoint(Fraction(1)),
        params={
            "radius": 8.0,
            "box": {"x": 0.35, "y": 0.35, "width": 0.05, "height": 0.05},
        },
    )
    compiled = compile_preview_video_effects([op])
    assert compiled is not None
    assert "scale=" in compiled.filter_complex
    assert "gblur" not in compiled.filter_complex
    assert "boxblur" not in compiled.filter_complex


def test_pixelate_compiles_scale_mosaic() -> None:
    op = Operation.create(
        OperationType.PIXELATE,
        ITEM_LEVEL_STREAM_INDEX,
        [],
        TimePoint(Fraction(0)),
        TimePoint(Fraction(1)),
        params={
            "size": 16.0,
            "box": {"x": 0.1, "y": 0.1, "width": 0.5, "height": 0.5},
        },
    )
    compiled = compile_preview_video_effects([op])
    assert compiled is not None
    graph = compiled.filter_complex
    assert "flags=neighbor" in graph
    assert "floor(iw/16)" in graph
    assert "enable='between(t,0,1)'" in graph


def test_zoom_compiles_punch_in_full_frame_overlay() -> None:
    op = Operation.create(
        OperationType.ZOOM,
        ITEM_LEVEL_STREAM_INDEX,
        [],
        TimePoint(Fraction(2)),
        TimePoint(Fraction(5)),
        params={"scale": 2.0, "x": 0.5, "y": 0.5},
    )
    compiled = compile_preview_video_effects([op])
    assert compiled is not None
    graph = compiled.filter_complex
    assert "exact=1" in graph
    assert "scale=" in graph
    assert "overlay=0:0:enable='between(t,2,5)'" in graph


def test_playback_lavfi_uses_project_matte_for_zoom() -> None:
    """Preserve-letterbox zoom keeps full-frame punch-in and paints bars back."""
    from corrcut.domain.video_matte import VideoMatteCache

    zoom = Operation.create(
        OperationType.ZOOM,
        ITEM_LEVEL_STREAM_INDEX,
        [],
        TimePoint(Fraction(1)),
        TimePoint(Fraction(2)),
        params={"scale": 2.0, "x": 0.5, "y": 0.5},
    )
    project = _project([zoom])
    assert project.source is not None
    project.source.probe = {
        "duration_seconds": 10.0,
        "video_streams": [{"width": 1920, "height": 1080}],
    }
    project.preserve_letterbox_on_zoom = True
    project.video_matte = VideoMatteCache(x=0.0, y=0.125, width=1.0, height=0.75)
    lavfi = compile_playback_lavfi(project, edits_enabled=True)
    assert lavfi is not None
    assert "overlay=0:0:enable=" in lavfi
    assert "scale=" in lavfi
    assert "drawbox=0:0:iw:135:black:t=fill" in lavfi
    assert "drawbox=0:945:iw:ih:black:t=fill" in lavfi


def test_zoom_preserve_letterbox_crops_matte_then_overlays() -> None:
    op = Operation.create(
        OperationType.ZOOM,
        ITEM_LEVEL_STREAM_INDEX,
        [],
        TimePoint(Fraction(2)),
        TimePoint(Fraction(5)),
        params={"scale": 2.0, "x": 0.5, "y": 0.5},
    )
    matte = {"x": 0.0, "y": 0.125, "width": 1.0, "height": 0.75}
    compiled = compile_preview_video_effects(
        [op],
        preserve_letterbox=True,
        matte=matte,
        frame_width=1920,
        frame_height=1080,
    )
    assert compiled is not None
    graph = compiled.filter_complex
    assert "scale=" in graph
    assert "drawbox=0:0:iw:135:black:t=fill" in graph
    assert "drawbox=0:945:iw:ih:black:t=fill" in graph
    assert "overlay=0:0:enable='between(t,2,5)'" in graph
    assert "pad=" not in graph


def test_zoom_preserve_without_frame_size_skips_bars() -> None:
    op = Operation.create(
        OperationType.ZOOM,
        ITEM_LEVEL_STREAM_INDEX,
        [],
        TimePoint(Fraction(2)),
        TimePoint(Fraction(5)),
        params={"scale": 2.0, "x": 0.5, "y": 0.5},
    )
    compiled = compile_preview_video_effects(
        [op],
        preserve_letterbox=True,
        matte={"x": 0.0, "y": 0.125, "width": 1.0, "height": 0.75},
    )
    assert compiled is not None
    assert "drawbox=0:0:iw:135" not in compiled.filter_complex


def test_zoom_preserve_off_keeps_full_frame_scale() -> None:
    op = Operation.create(
        OperationType.ZOOM,
        ITEM_LEVEL_STREAM_INDEX,
        [],
        TimePoint(Fraction(2)),
        TimePoint(Fraction(5)),
        params={"scale": 2.0, "x": 0.5, "y": 0.5},
    )
    matte = {"x": 0.0, "y": 0.125, "width": 1.0, "height": 0.75}
    compiled = compile_preview_video_effects(
        [op],
        preserve_letterbox=False,
        matte=matte,
    )
    assert compiled is not None
    graph = compiled.filter_complex
    assert "overlay=0:0:enable='between(t,2,5)'" in graph
    assert "floor(ih*0.125)" not in graph


def test_zoom_non_square_box_locks_source_aspect() -> None:
    op = Operation.create(
        OperationType.ZOOM,
        ITEM_LEVEL_STREAM_INDEX,
        [],
        TimePoint(Fraction(2)),
        TimePoint(Fraction(5)),
        params={"box": {"x": 0.1, "y": 0.1, "width": 0.5, "height": 0.2}},
    )
    boxes = _box_xywh(op)
    assert boxes is not None
    start, end = boxes
    assert start["width"] == start["height"]
    assert end["width"] == end["height"]
    assert start["width"] == 0.2


def test_crop_keeps_native_window_without_magnification() -> None:
    op = Operation.create(
        OperationType.CROP,
        ITEM_LEVEL_STREAM_INDEX,
        [],
        TimePoint(Fraction(1)),
        TimePoint(Fraction(2)),
        params={"scale": 2.0, "x": 0.5, "y": 0.5},
    )
    compiled = compile_preview_video_effects([op])
    assert compiled is not None
    graph = compiled.filter_complex
    # Crop = black frame + native-scale window; must not scale the window up.
    assert "drawbox=0:0:iw:ih:black:t=fill" in graph
    assert "exact=1" in graph
    assert "scale=" not in graph
    assert "overlay=0:0:enable='between(t,1,2)'" in graph
    assert "floor(main_w*" in graph


def test_playback_lavfi_includes_blur() -> None:
    blur = Operation.create(
        OperationType.BLUR,
        ITEM_LEVEL_STREAM_INDEX,
        [],
        TimePoint(Fraction(1)),
        TimePoint(Fraction(2)),
    )
    project = _project([blur])
    lavfi = compile_playback_lavfi(project, edits_enabled=True)
    assert lavfi is not None
    assert "scale=" in lavfi
    assert "gblur" not in lavfi
    assert MPV_VIDEO_IN in lavfi
    assert MPV_AUDIO_OUT in lavfi


def test_playback_lavfi_ab_original_keeps_video_and_audio_pads() -> None:
    blank = Operation.create(
        OperationType.BLANK,
        ITEM_LEVEL_STREAM_INDEX,
        [],
        TimePoint(Fraction(1)),
        TimePoint(Fraction(2)),
    )
    project = _project([blank])
    lavfi = compile_playback_lavfi(project, edits_enabled=False)
    assert lavfi == MPV_PASSTHROUGH
    assert MPV_VIDEO_IN in lavfi
    assert MPV_VIDEO_OUT in lavfi
    assert MPV_AUDIO_IN in lavfi
    assert MPV_AUDIO_OUT in lavfi
    assert "drawbox" not in lavfi


def test_playback_lavfi_omits_zoom_when_apply_zoom_false() -> None:
    zoom = Operation.create(
        OperationType.ZOOM,
        ITEM_LEVEL_STREAM_INDEX,
        [],
        TimePoint(Fraction(1)),
        TimePoint(Fraction(2)),
        params={"box": {"x": 0.25, "y": 0.25, "width": 0.5, "height": 0.5}},
    )
    project = _project([zoom])
    paused = compile_playback_lavfi(project, edits_enabled=True, apply_zoom=False)
    playing = compile_playback_lavfi(project, edits_enabled=True, apply_zoom=True)
    assert paused == MPV_PASSTHROUGH
    assert playing is not None
    assert "scale=" in playing
    assert "overlay=0:0" in playing


def test_playback_lavfi_keeps_cover_when_zoom_omitted() -> None:
    zoom = Operation.create(
        OperationType.ZOOM,
        ITEM_LEVEL_STREAM_INDEX,
        [],
        TimePoint(Fraction(1)),
        TimePoint(Fraction(2)),
        params={"box": {"x": 0.25, "y": 0.25, "width": 0.5, "height": 0.5}},
    )
    cover = Operation.create(
        OperationType.COVER,
        ITEM_LEVEL_STREAM_INDEX,
        [],
        TimePoint(Fraction(3)),
        TimePoint(Fraction(4)),
        params={"box": {"x": 0.1, "y": 0.1, "width": 0.2, "height": 0.2}},
    )
    project = _project([zoom, cover])
    paused = compile_playback_lavfi(project, edits_enabled=True, apply_zoom=False)
    assert paused is not None
    assert "drawbox" in paused
    assert "overlay=0:0" not in paused


def test_time_window_drops_distant_blur_segments() -> None:
    near = Operation.create(
        OperationType.BLUR,
        ITEM_LEVEL_STREAM_INDEX,
        [],
        TimePoint(Fraction(10)),
        TimePoint(Fraction(12)),
        params={"box": {"x": 0.2, "y": 0.2, "width": 0.3, "height": 0.3}, "radius": 8},
    )
    far = Operation.create(
        OperationType.BLUR,
        ITEM_LEVEL_STREAM_INDEX,
        [],
        TimePoint(Fraction(200)),
        TimePoint(Fraction(205)),
        params={"box": {"x": 0.2, "y": 0.2, "width": 0.3, "height": 0.3}, "radius": 8},
    )
    full = compile_preview_video_effects([near, far])
    windowed = compile_preview_video_effects([near, far], time_window=(0.0, 40.0))
    assert full is not None and windowed is not None
    assert full.filter_complex.count("split=2") == 2
    assert windowed.filter_complex.count("split=2") == 1
    assert "between(t,10,12)" in windowed.filter_complex
    assert "between(t,200,205)" not in windowed.filter_complex

    project = _project([near, far])
    lavfi = compile_playback_lavfi(project, edits_enabled=True, playhead_s=11.0)
    assert lavfi is not None
    assert lavfi.count("split=2") == 1
