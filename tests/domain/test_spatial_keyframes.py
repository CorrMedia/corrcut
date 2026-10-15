"""Keyframe poses for spatial video operations."""

from __future__ import annotations

from fractions import Fraction

import pytest

from corrcut.domain.operation import Operation, OperationType
from corrcut.domain.spatial_keyframes import (
    SpatialKeyframe,
    add_spatial_keyframe,
    clip_box_to_frame,
    expand_spatial_operation,
    has_spatial_animation,
    keep_box_visible,
    lock_box_to_matte_aspect,
    lock_box_to_source_aspect,
    merge_abutting_spatial_operations,
    move_spatial_keyframe,
    normalize_box,
    remove_spatial_keyframe,
    retarget_spatial_keyframes,
    neighbor_pose_boxes,
    preview_box_at_seconds,
    sample_box_at_seconds,
    set_spatial_keyframes,
    spatial_keyframes,
    zoom_box_in_matte,
)
from corrcut.domain.time_point import TimePoint


def _cover(
    start: float,
    end: float,
    *,
    params: dict | None = None,
) -> Operation:
    return Operation.create(
        OperationType.COVER,
        -1,
        [],
        TimePoint(Fraction(start)),
        TimePoint(Fraction(end)),
        params=params or {"box": {"x": 0.1, "y": 0.2, "width": 0.2, "height": 0.25}},
    )


def _box(x: float) -> dict[str, float]:
    return {"x": x, "y": 0.1, "width": 0.2, "height": 0.2}


def test_static_box_is_one_keyframe() -> None:
    op = _cover(1, 3)
    kfs = spatial_keyframes(op)
    assert len(kfs) == 1
    assert kfs[0].seconds == 1.0
    assert kfs[0].box["x"] == 0.1
    assert not has_spatial_animation(op)


def test_box_end_is_two_keyframes() -> None:
    op = _cover(
        1,
        3,
        params={
            "box": {"x": 0.1, "y": 0.2, "width": 0.2, "height": 0.25},
            "box_end": {"x": 0.6, "y": 0.5, "width": 0.2, "height": 0.25},
        },
    )
    kfs = spatial_keyframes(op)
    assert len(kfs) == 2
    assert kfs[0].seconds == 1.0
    assert kfs[1].seconds == 3.0
    assert kfs[1].box["x"] == 0.6
    assert expand_spatial_operation(op) == [op]


def test_zoom_center_end_is_two_keyframes() -> None:
    op = Operation.create(
        OperationType.ZOOM,
        -1,
        [],
        TimePoint(Fraction(0)),
        TimePoint(Fraction(2)),
        params={"scale": 2.0, "x": 0.3, "y": 0.5, "x_end": 0.7},
    )
    kfs = spatial_keyframes(op)
    assert len(kfs) == 2
    assert kfs[0].box["x"] < kfs[1].box["x"]
    assert kfs[0].seconds == 0.0
    assert kfs[1].seconds == 2.0


def test_add_at_existing_time_selects_pose() -> None:
    op = _cover(0, 10)
    index = add_spatial_keyframe(op, 0.0)
    assert index == 0
    assert len(spatial_keyframes(op)) == 1
    assert not has_spatial_animation(op)


def test_add_keyframe_outside_region_keeps_source_time() -> None:
    op = _cover(10, 17)
    index = add_spatial_keyframe(op, 8.0)
    kfs = spatial_keyframes(op)
    assert index == 0
    assert abs(kfs[0].seconds - 8.0) < 1e-6
    index = add_spatial_keyframe(op, 19.0, _box(0.7))
    kfs = spatial_keyframes(op)
    assert index == 2
    assert abs(kfs[2].seconds - 19.0) < 1e-6
    assert kfs[2].box["x"] == 0.7


def test_move_keyframe_outside_region() -> None:
    op = _cover(10, 17)
    set_spatial_keyframes(
        op,
        [
            SpatialKeyframe(13.0, _box(0.1)),
            SpatialKeyframe(15.0, _box(0.6)),
        ],
    )
    index = move_spatial_keyframe(op, 0, 8.5)
    kfs = spatial_keyframes(op)
    assert index == 0
    assert abs(kfs[0].seconds - 8.5) < 1e-6
    assert abs(kfs[1].seconds - 15.0) < 1e-6


def test_export_samples_edges_when_keys_are_outside() -> None:
    op = _cover(10, 17)
    set_spatial_keyframes(
        op,
        [
            SpatialKeyframe(8.0, _box(0.0)),
            SpatialKeyframe(18.0, _box(1.0)),
        ],
    )
    segs = expand_spatial_operation(op)
    assert float(segs[0].time_range.start.seconds) == 10
    assert float(segs[-1].time_range.end.seconds) == 17
    start_box = segs[0].params["box"]
    assert start_box["x"] == sample_box_at_seconds(spatial_keyframes(op), 10.0)["x"]


def test_add_keyframe_on_static_box_at_playhead() -> None:
    op = _cover(0, 10)
    index = add_spatial_keyframe(op, 4.0)
    assert index == 1
    kfs = spatial_keyframes(op)
    assert len(kfs) == 2
    assert abs(kfs[1].seconds - 4.0) < 1e-6
    assert kfs[0].box == kfs[1].box
    assert "box_keyframes" in op.params
    assert abs(op.params["box_keyframes"][1]["seconds"] - 4.0) < 1e-6


def test_add_keyframe_samples_current_lerp() -> None:
    op = _cover(
        0,
        10,
        params={
            "box": {"x": 0.1, "y": 0.1, "width": 0.2, "height": 0.2},
            "box_end": {"x": 0.6, "y": 0.1, "width": 0.2, "height": 0.2},
        },
    )
    index = add_spatial_keyframe(op, 4.0)
    assert index == 1
    kfs = spatial_keyframes(op)
    assert len(kfs) == 3
    assert abs(kfs[1].seconds - 4.0) < 1e-6
    assert "box_keyframes" in op.params
    assert abs(kfs[1].box["x"] - 0.3) < 1e-6


def test_legacy_normalized_offsets_are_imported() -> None:
    op = _cover(10, 20)
    op.params["box_keyframes"] = [
        {"offset": 0.0, "box": _box(0.1)},
        {"offset": 0.4, "box": _box(0.35)},
        {"offset": 1.0, "box": _box(0.6)},
    ]
    kfs = spatial_keyframes(op)
    assert len(kfs) == 3
    assert abs(kfs[1].seconds - 14.0) < 1e-6


def test_move_keyframe_retimes_pose() -> None:
    op = _cover(0, 10)
    set_spatial_keyframes(
        op,
        [
            SpatialKeyframe(0.0, _box(0.1)),
            SpatialKeyframe(4.0, _box(0.4)),
        ],
    )
    index = move_spatial_keyframe(op, 1, 7.5)
    kfs = spatial_keyframes(op)
    assert index == 1
    assert abs(kfs[1].seconds - 7.5) < 1e-6
    assert kfs[1].box["x"] == 0.4
    index = move_spatial_keyframe(op, 1, 0.0)
    kfs = spatial_keyframes(op)
    assert index == 0
    assert len(kfs) == 1


def test_remove_keyframe_can_drop_any_pose() -> None:
    op = _cover(0, 4)
    set_spatial_keyframes(
        op,
        [
            SpatialKeyframe(0.0, _box(0.1)),
            SpatialKeyframe(2.0, _box(0.3)),
            SpatialKeyframe(4.0, _box(0.6)),
        ],
    )
    assert remove_spatial_keyframe(op, 1) == 1
    assert len(spatial_keyframes(op)) == 2
    assert remove_spatial_keyframe(op, 1) == 0
    kfs = spatial_keyframes(op)
    assert len(kfs) == 1
    assert not has_spatial_animation(op)
    assert kfs[0].box["x"] == 0.1


def test_remove_end_pose_does_not_recreate_it() -> None:
    op = _cover(0, 4)
    set_spatial_keyframes(
        op,
        [
            SpatialKeyframe(0.0, _box(0.1)),
            SpatialKeyframe(1.6, _box(0.3)),
            SpatialKeyframe(4.0, _box(0.6)),
        ],
    )
    assert remove_spatial_keyframe(op, 2) == 1
    kfs = spatial_keyframes(op)
    assert len(kfs) == 2
    assert abs(kfs[1].seconds - 1.6) < 1e-6
    assert kfs[1].box["x"] == 0.3


def test_hold_move_hold_expands_and_roundtrips() -> None:
    op = _cover(10, 17)
    set_spatial_keyframes(
        op,
        [
            SpatialKeyframe(13.0, _box(0.1)),
            SpatialKeyframe(15.0, _box(0.6)),
        ],
    )
    segs = expand_spatial_operation(op)
    assert len(segs) == 3
    assert float(segs[0].time_range.start.seconds) == 10
    assert abs(float(segs[0].time_range.end.seconds) - 13) < 1e-6
    assert segs[0].params["box"]["x"] == segs[0].params["box_end"]["x"] == 0.1
    assert abs(float(segs[1].time_range.start.seconds) - 13) < 1e-6
    assert abs(float(segs[1].time_range.end.seconds) - 15) < 1e-6
    assert segs[1].params["box"]["x"] == 0.1
    assert segs[1].params["box_end"]["x"] == 0.6
    assert abs(float(segs[2].time_range.start.seconds) - 15) < 1e-6
    assert float(segs[2].time_range.end.seconds) == 17
    assert segs[2].params["box"]["x"] == segs[2].params["box_end"]["x"] == 0.6
    merged = merge_abutting_spatial_operations(segs)
    assert len(merged) == 1
    kfs = spatial_keyframes(merged[0])
    assert len(kfs) == 2
    assert abs(kfs[0].seconds - 13.0) < 1e-4
    assert abs(kfs[1].seconds - 15.0) < 1e-4


def test_expand_three_keyframes_to_abutting_segments() -> None:
    op = _cover(10, 20)
    set_spatial_keyframes(
        op,
        [
            SpatialKeyframe(10.0, _box(0.1)),
            SpatialKeyframe(15.0, _box(0.3)),
            SpatialKeyframe(20.0, _box(0.6)),
        ],
    )
    segs = expand_spatial_operation(op)
    assert len(segs) == 2
    assert float(segs[0].time_range.start.seconds) == 10
    assert float(segs[0].time_range.end.seconds) == 15
    assert float(segs[1].time_range.start.seconds) == 15
    assert float(segs[1].time_range.end.seconds) == 20
    assert segs[0].params["box"]["x"] == 0.1
    assert segs[0].params["box_end"]["x"] == 0.3
    assert segs[1].params["box"]["x"] == 0.3
    assert segs[1].params["box_end"]["x"] == 0.6
    assert "box_keyframes" not in segs[0].params
    assert segs[0].id == op.id
    assert segs[1].id == f"{op.id}__k1"


def test_merge_abutting_edits_restores_keyframes() -> None:
    op = _cover(10, 20)
    set_spatial_keyframes(
        op,
        [
            SpatialKeyframe(10.0, _box(0.1)),
            SpatialKeyframe(15.0, _box(0.3)),
            SpatialKeyframe(20.0, _box(0.6)),
        ],
    )
    segs = expand_spatial_operation(op)
    merged = merge_abutting_spatial_operations(segs)
    assert len(merged) == 1
    kfs = spatial_keyframes(merged[0])
    assert len(kfs) == 3
    assert abs(kfs[1].seconds - 15.0) < 1e-4
    assert float(merged[0].time_range.start.seconds) == 10
    assert float(merged[0].time_range.end.seconds) == 20


def test_does_not_merge_disjoint_or_mismatched_boxes() -> None:
    a = _cover(1, 2, params={"box": _box(0.1)})
    b = _cover(3, 4, params={"box": _box(0.1)})
    assert len(merge_abutting_spatial_operations([a, b])) == 2
    c = _cover(2, 3, params={"box": _box(0.5)})
    assert len(merge_abutting_spatial_operations([a, c])) == 2


def test_sample_box_lerps_between_poses() -> None:
    kfs = [
        SpatialKeyframe(0.0, {"x": 0.0, "y": 0.0, "width": 0.2, "height": 0.2}),
        SpatialKeyframe(1.0, {"x": 0.8, "y": 0.4, "width": 0.2, "height": 0.2}),
    ]
    mid = sample_box_at_seconds(kfs, 0.5)
    assert abs(mid["x"] - 0.4) < 1e-9
    assert abs(mid["y"] - 0.2) < 1e-9


def test_preview_box_holds_larger_size_and_moves_center() -> None:
    kfs = [
        SpatialKeyframe(0.0, {"x": 0.0, "y": 0.0, "width": 0.2, "height": 0.2}),
        SpatialKeyframe(1.0, {"x": 0.6, "y": 0.2, "width": 0.4, "height": 0.4}),
    ]
    mid = preview_box_at_seconds(kfs, 0.5)
    assert mid["width"] == pytest.approx(0.4)
    assert mid["height"] == pytest.approx(0.4)
    assert mid["x"] == pytest.approx(0.25)
    assert mid["y"] == pytest.approx(0.05)
    assert preview_box_at_seconds(kfs, 0.0)["width"] == pytest.approx(0.2)


def test_neighbor_poses_are_only_the_bracketing_keys() -> None:
    kfs = [
        SpatialKeyframe(0.0, {"x": 0.0, "y": 0.0, "width": 0.2, "height": 0.2}),
        SpatialKeyframe(1.0, {"x": 0.3, "y": 0.3, "width": 0.2, "height": 0.2}),
        SpatialKeyframe(2.0, {"x": 0.6, "y": 0.1, "width": 0.2, "height": 0.2}),
    ]
    between = neighbor_pose_boxes(kfs, 0.4)
    assert [box["x"] for box in between] == pytest.approx([0.0, 0.3])
    on_middle = neighbor_pose_boxes(kfs, 1.0)
    assert [box["x"] for box in on_middle] == pytest.approx([0.0, 0.6])


def test_trim_does_not_move_pose_times() -> None:
    op = _cover(10, 17)
    set_spatial_keyframes(
        op,
        [
            SpatialKeyframe(13.0, _box(0.1)),
            SpatialKeyframe(15.0, _box(0.6)),
        ],
    )
    retarget_spatial_keyframes(op, 10.0, 17.0, 10.0, 20.0)
    kfs = spatial_keyframes(op)
    assert abs(kfs[0].seconds - 13.0) < 1e-6
    assert abs(kfs[1].seconds - 15.0) < 1e-6
    retarget_spatial_keyframes(op, 10.0, 20.0, 12.0, 20.0)
    kfs = spatial_keyframes(op)
    assert abs(kfs[0].seconds - 13.0) < 1e-6
    assert abs(kfs[1].seconds - 15.0) < 1e-6


def test_slide_does_not_move_pose_times() -> None:
    op = _cover(10, 17)
    set_spatial_keyframes(
        op,
        [
            SpatialKeyframe(10.0, _box(0.1)),
            SpatialKeyframe(12.0, _box(0.6)),
        ],
    )
    op.time_range.start = TimePoint(Fraction(12))
    op.time_range.end = TimePoint(Fraction(16))
    retarget_spatial_keyframes(op, 10.0, 17.0, 12.0, 16.0)
    kfs = spatial_keyframes(op)
    assert abs(kfs[0].seconds - 10.0) < 1e-6
    assert abs(kfs[1].seconds - 12.0) < 1e-6


def test_lock_box_to_source_aspect_is_square() -> None:
    locked = lock_box_to_source_aspect({"x": 0.1, "y": 0.2, "width": 0.5, "height": 0.2})
    assert locked["width"] == locked["height"]


def test_zoom_box_in_matte_keeps_matte_aspect() -> None:
    matte = {"x": 0.0, "y": 0.125, "width": 1.0, "height": 0.75}
    box = zoom_box_in_matte(0.5, 0.5, 2.0, matte)
    assert box["width"] == pytest.approx(0.5)
    assert box["height"] == pytest.approx(0.375)
    assert box["x"] >= matte["x"]
    assert box["y"] >= matte["y"]
    assert box["x"] + box["width"] <= matte["x"] + matte["width"] + 1e-9
    assert box["y"] + box["height"] <= matte["y"] + matte["height"] + 1e-9


def test_lock_box_to_matte_aspect_matches_matte_ratio() -> None:
    matte = {"x": 0.0, "y": 0.1, "width": 1.0, "height": 0.8}
    locked = lock_box_to_matte_aspect(
        {"x": 0.2, "y": 0.2, "width": 0.5, "height": 0.2},
        matte,
    )
    assert locked["width"] / locked["height"] == pytest.approx(matte["width"] / matte["height"])
    assert locked["y"] >= matte["y"] - 1e-9
    assert locked["y"] + locked["height"] <= matte["y"] + matte["height"] + 1e-9


def test_cover_preserves_overflow_box() -> None:
    op = _cover(
        1,
        3,
        params={"box": {"x": -0.15, "y": 0.2, "width": 0.4, "height": 0.3}},
    )
    kfs = spatial_keyframes(op)
    assert abs(kfs[0].box["x"] + 0.15) < 1e-9
    assert abs(kfs[0].box["width"] - 0.4) < 1e-9


def test_cover_preserves_overflow_box_end() -> None:
    op = _cover(
        1,
        3,
        params={
            "box": {"x": -0.2, "y": 0.2, "width": 0.4, "height": 0.3},
            "box_end": {"x": 0.85, "y": 0.2, "width": 0.4, "height": 0.3},
        },
    )
    kfs = spatial_keyframes(op)
    assert kfs[0].box["x"] < 0.0
    assert kfs[1].box["x"] + kfs[1].box["width"] > 1.0


def test_zoom_clamps_overflow_box() -> None:
    op = Operation.create(
        OperationType.ZOOM,
        -1,
        [],
        TimePoint(Fraction(0)),
        TimePoint(Fraction(2)),
        params={"scale": 2.0, "box": {"x": -0.15, "y": 0.2, "width": 0.4, "height": 0.4}},
    )
    kfs = spatial_keyframes(op)
    assert kfs[0].box["x"] >= 0.0
    assert kfs[0].box["x"] + kfs[0].box["width"] <= 1.0 + 1e-9


def test_normalize_box_overflow_keeps_negative_origin() -> None:
    box = normalize_box({"x": -0.1, "y": 0.2, "width": 0.3, "height": 0.4}, overflow=True)
    assert box is not None
    assert abs(box["x"] + 0.1) < 1e-9
    assert abs(box["width"] - 0.3) < 1e-9
    clamped = normalize_box({"x": -0.1, "y": 0.2, "width": 0.3, "height": 0.4})
    assert clamped is not None
    assert clamped["x"] == 0.0


def test_clip_box_to_frame_left_overflow() -> None:
    clipped = clip_box_to_frame({"x": -0.1, "y": 0.2, "width": 0.3, "height": 0.4})
    assert clipped is not None
    assert abs(clipped["x"]) < 1e-9
    assert abs(clipped["width"] - 0.2) < 1e-9
    assert abs(clipped["y"] - 0.2) < 1e-9
    assert abs(clipped["height"] - 0.4) < 1e-9
    assert clip_box_to_frame({"x": -0.4, "y": 0.2, "width": 0.2, "height": 0.3}) is None


def test_keep_box_visible_stops_full_exit() -> None:
    kept = keep_box_visible({"x": -0.5, "y": 0.2, "width": 0.3, "height": 0.3})
    visible = clip_box_to_frame(kept)
    assert visible is not None
    assert abs(visible["width"] - 0.02) < 1e-9
