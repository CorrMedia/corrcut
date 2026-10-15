"""Unit tests for stacked timeline lane geometry."""

from corrcut.domain.operation import OperationType
from corrcut.ui.timeline.timeline_lanes import (
    AUDIO_LANE_HEIGHT,
    AUDIO_TOP,
    GUTTER_TOP,
    LANE_HEIGHT,
    MAX_LANE_HEIGHT,
    MEDIA_TOP,
    MIN_LANE_HEIGHT,
    RULER_HEIGHT,
    SCENE_HEIGHT,
    TRACK_GUTTER,
    VIDEO_LANE_HEIGHT,
    VIDEO_TOP,
    TimelineLane,
    is_track_gutter_y,
    lane_at_y,
    lane_for_operation_type,
    lane_height_for_viewport,
    lane_rect,
    lane_y_band,
    scene_height_for,
)


def test_video_lane_is_tall_enough_for_filmstrip() -> None:
    assert VIDEO_LANE_HEIGHT >= 64.0
    assert VIDEO_LANE_HEIGHT == AUDIO_LANE_HEIGHT
    assert MIN_LANE_HEIGHT == 65.0
    assert LANE_HEIGHT == 85.0
    assert MAX_LANE_HEIGHT == 105.0


def test_lane_height_shrinks_to_fit_viewport_and_stops_at_minimum() -> None:
    preferred_viewport = scene_height_for(LANE_HEIGHT) + 2.0
    assert lane_height_for_viewport(preferred_viewport) == LANE_HEIGHT
    assert lane_height_for_viewport(scene_height_for(95.0) + 2.0) == 95.0
    assert lane_height_for_viewport(scene_height_for(MAX_LANE_HEIGHT) + 40.0) == MAX_LANE_HEIGHT
    assert lane_height_for_viewport(10.0) == MIN_LANE_HEIGHT


def test_lane_at_y_splits_ruler_video_audio() -> None:
    assert lane_at_y(0.0) is None
    assert lane_at_y(VIDEO_TOP + 1.0) is TimelineLane.VIDEO
    assert lane_at_y(GUTTER_TOP + 1.0) is None
    assert lane_at_y(AUDIO_TOP + 1.0) is TimelineLane.AUDIO


def test_track_gutter_sits_between_video_and_audio() -> None:
    assert TRACK_GUTTER > 1.0
    assert GUTTER_TOP == VIDEO_TOP + VIDEO_LANE_HEIGHT
    assert AUDIO_TOP == GUTTER_TOP + TRACK_GUTTER
    assert is_track_gutter_y(GUTTER_TOP)
    assert not is_track_gutter_y(GUTTER_TOP - 1.0)
    assert not is_track_gutter_y(AUDIO_TOP)


def test_lane_for_operation_type_mapping() -> None:
    assert lane_for_operation_type(OperationType.MUTE) is TimelineLane.AUDIO
    assert lane_for_operation_type(OperationType.VOLUME) is TimelineLane.AUDIO
    assert lane_for_operation_type(OperationType.BEEP) is TimelineLane.AUDIO
    assert lane_for_operation_type(OperationType.SKIP) is TimelineLane.SKIP
    assert lane_for_operation_type(OperationType.COVER) is TimelineLane.VIDEO
    assert lane_for_operation_type(OperationType.BLUR) is TimelineLane.VIDEO
    assert lane_for_operation_type(OperationType.BLANK) is TimelineLane.VIDEO
    assert lane_for_operation_type(OperationType.ZOOM) is TimelineLane.VIDEO
    assert lane_for_operation_type(OperationType.CROP) is TimelineLane.VIDEO
    assert lane_for_operation_type(OperationType.PIXELATE) is TimelineLane.VIDEO


def test_audio_and_video_lane_rects_do_not_overlap_y() -> None:
    audio = lane_rect(TimelineLane.AUDIO, 1.0, 2.0, pps=80.0, scroll_x=0.0)
    video = lane_rect(TimelineLane.VIDEO, 1.0, 2.0, pps=80.0, scroll_x=0.0)
    assert audio.bottom() <= video.top() or video.bottom() <= audio.top()
    assert video.top() >= VIDEO_TOP
    assert audio.top() >= AUDIO_TOP


def test_skip_covers_video_and_audio_below_ruler() -> None:
    skip = lane_rect(TimelineLane.SKIP, 1.0, 3.0, pps=80.0, scroll_x=0.0)
    top, height = lane_y_band(TimelineLane.SKIP)
    assert top == MEDIA_TOP
    assert top >= RULER_HEIGHT
    assert top + height == SCENE_HEIGHT
    assert skip.top() >= MEDIA_TOP
    assert skip.bottom() <= SCENE_HEIGHT


def test_lane_rect_x_follows_scroll() -> None:
    rect = lane_rect(TimelineLane.AUDIO, 10.0, 12.0, pps=100.0, scroll_x=500.0)
    assert rect.left() == 500.0
