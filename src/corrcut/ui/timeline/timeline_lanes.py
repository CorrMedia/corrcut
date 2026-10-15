"""Vertical lane geometry for the stacked timeline (ruler / video / audio)."""

from __future__ import annotations

from enum import Enum

from PySide6.QtCore import QRectF

from corrcut.domain.operation import OperationType, is_audio_operation
from corrcut.ui.timeline.region_geometry import MIN_REGION_DURATION_S

RULER_HEIGHT = 24.0
LANE_HEIGHT = 85.0
MIN_LANE_HEIGHT = 65.0
MAX_LANE_HEIGHT = 105.0
VIDEO_LANE_HEIGHT = LANE_HEIGHT
AUDIO_LANE_HEIGHT = LANE_HEIGHT
LANE_DIVIDER = 1.0
# Dark band between the video and audio lanes.
TRACK_GUTTER = 6.0
# Vertical inset inside video/audio region rects (mirrors the old 8px pad).
REGION_PAD_Y = 4.0

MEDIA_TOP = RULER_HEIGHT + LANE_DIVIDER
VIDEO_TOP = MEDIA_TOP
GUTTER_TOP = VIDEO_TOP + VIDEO_LANE_HEIGHT
AUDIO_TOP = GUTTER_TOP + TRACK_GUTTER
SCENE_HEIGHT = AUDIO_TOP + AUDIO_LANE_HEIGHT


def gutter_top_for(lane_height: float) -> float:
    return VIDEO_TOP + lane_height


def audio_top_for(lane_height: float) -> float:
    return gutter_top_for(lane_height) + TRACK_GUTTER


def scene_height_for(lane_height: float) -> float:
    return audio_top_for(lane_height) + lane_height


def lane_height_for_viewport(viewport_height: float) -> float:
    """Shared lane height for the viewport: 65px minimum, 85px default, 105px maximum."""
    chrome = MEDIA_TOP + TRACK_GUTTER
    # Widget height includes 2px of slack past the scene so rounding does not shrink the lanes.
    fitted = (viewport_height - chrome - 2.0) / 2.0
    if fitted >= MAX_LANE_HEIGHT:
        return MAX_LANE_HEIGHT
    return max(MIN_LANE_HEIGHT, fitted)


class TimelineLane(Enum):
    VIDEO = "video"
    AUDIO = "audio"
    SKIP = "skip"


def lane_for_operation_type(op_type: OperationType) -> TimelineLane:
    if op_type == OperationType.SKIP:
        return TimelineLane.SKIP
    if is_audio_operation(op_type):
        return TimelineLane.AUDIO
    return TimelineLane.VIDEO


def lane_y_band(lane: TimelineLane, *, lane_height: float | None = None) -> tuple[float, float]:
    """Return (top, height) for a lane's content band."""
    height = LANE_HEIGHT if lane_height is None else lane_height
    if lane is TimelineLane.VIDEO:
        return VIDEO_TOP, height
    if lane is TimelineLane.AUDIO:
        return audio_top_for(height), height
    # Skip spans video + audio (not the ruler).
    return MEDIA_TOP, scene_height_for(height) - MEDIA_TOP


def lane_rect(
    lane: TimelineLane,
    start_s: float,
    end_s: float,
    pps: float,
    scroll_x: float,
    *,
    pad_y: float | None = None,
    lane_height: float | None = None,
) -> QRectF:
    """Scene rect for a time range on the given lane."""
    lo = min(start_s, end_s)
    hi = max(start_s, end_s)
    x = lo * pps - scroll_x
    w = max(MIN_REGION_DURATION_S * pps, (hi - lo) * pps)
    top, height = lane_y_band(lane, lane_height=lane_height)
    if pad_y is None:
        pad_y = REGION_PAD_Y if lane is not TimelineLane.SKIP else 2.0
    pad = min(pad_y, height / 4.0)
    return QRectF(x, top + pad, w, max(1.0, height - 2.0 * pad))


def audio_lane_mid_y(lane_height: float | None = None) -> float:
    height = LANE_HEIGHT if lane_height is None else lane_height
    return audio_top_for(height) + height / 2.0


def video_lane_mid_y(lane_height: float | None = None) -> float:
    height = LANE_HEIGHT if lane_height is None else lane_height
    return VIDEO_TOP + height / 2.0


def is_ruler_y(scene_y: float) -> bool:
    return scene_y < MEDIA_TOP


def is_track_gutter_y(scene_y: float, lane_height: float | None = None) -> bool:
    height = LANE_HEIGHT if lane_height is None else lane_height
    return gutter_top_for(height) <= scene_y < audio_top_for(height)


def lane_at_y(scene_y: float, lane_height: float | None = None) -> TimelineLane | None:
    """Lane under a scene Y, or None on the ruler or the track gutter."""
    height = LANE_HEIGHT if lane_height is None else lane_height
    if scene_y < MEDIA_TOP or is_track_gutter_y(scene_y, height):
        return None
    if scene_y < gutter_top_for(height):
        return TimelineLane.VIDEO
    return TimelineLane.AUDIO
