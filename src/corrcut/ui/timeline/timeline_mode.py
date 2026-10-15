"""Timeline interaction mode (select vs create regions)."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from corrcut.domain.operation import OperationType


class TimelineMode(Enum):
    SELECT = "select"
    CREATE_MUTE = "create_mute"
    CREATE_VOLUME = "create_volume"
    CREATE_BEEP = "create_beep"
    CREATE_SKIP = "create_skip"
    CREATE_BLANK = "create_blank"
    CREATE_COVER = "create_cover"
    CREATE_BLUR = "create_blur"
    CREATE_PIXELATE = "create_pixelate"
    CREATE_ZOOM = "create_zoom"
    CREATE_CROP = "create_crop"

    @property
    def create_operation_type(self) -> OperationType | None:
        mapping = {
            TimelineMode.CREATE_MUTE: OperationType.MUTE,
            TimelineMode.CREATE_VOLUME: OperationType.VOLUME,
            TimelineMode.CREATE_BEEP: OperationType.BEEP,
            TimelineMode.CREATE_SKIP: OperationType.SKIP,
            TimelineMode.CREATE_BLANK: OperationType.BLANK,
            TimelineMode.CREATE_COVER: OperationType.COVER,
            TimelineMode.CREATE_BLUR: OperationType.BLUR,
            TimelineMode.CREATE_PIXELATE: OperationType.PIXELATE,
            TimelineMode.CREATE_ZOOM: OperationType.ZOOM,
            TimelineMode.CREATE_CROP: OperationType.CROP,
        }
        return mapping.get(self)

    @classmethod
    def from_toolbar_label(cls, label: str) -> TimelineMode:
        for mode in cls:
            if mode.value == label:
                return mode
        return cls.SELECT

    @classmethod
    def for_operation_type(cls, op_type: OperationType) -> TimelineMode:
        for mode in cls:
            if mode.create_operation_type == op_type:
                return mode
        return cls.SELECT


@dataclass(frozen=True)
class ToolStripItem:
    mode: TimelineMode
    label: str
    tip: str


# Compact tool-strip order: select, then audio, cut, video.
TOOL_STRIP_ITEMS: tuple[ToolStripItem, ...] = (
    ToolStripItem(TimelineMode.SELECT, "Select", "Deselect — empty-lane drag still draws (V)"),
    ToolStripItem(TimelineMode.CREATE_MUTE, "Mute", "Mute selected edit, or next audio draw (M)"),
    ToolStripItem(TimelineMode.CREATE_VOLUME, "Volume", "Volume selected edit, or next audio draw"),
    ToolStripItem(TimelineMode.CREATE_BEEP, "Beep", "Beep selected edit, or next audio draw (B)"),
    ToolStripItem(TimelineMode.CREATE_SKIP, "Skip", "Skip selected edit, or next draw"),
    ToolStripItem(TimelineMode.CREATE_BLANK, "Blank", "Blank selected edit, or next video draw"),
    ToolStripItem(TimelineMode.CREATE_COVER, "Cover", "Cover selected edit, or next video draw"),
    ToolStripItem(TimelineMode.CREATE_BLUR, "Blur", "Blur selected edit, or next video draw"),
    ToolStripItem(
        TimelineMode.CREATE_PIXELATE, "Pixelate", "Pixelate selected edit, or next video draw"
    ),
    ToolStripItem(TimelineMode.CREATE_ZOOM, "Zoom", "Zoom selected edit, or next video draw"),
    ToolStripItem(TimelineMode.CREATE_CROP, "Crop", "Crop selected edit, or next video draw"),
)

TIMELINE_CREATE_MODES: tuple[TimelineMode, ...] = tuple(
    item.mode for item in TOOL_STRIP_ITEMS if item.mode is not TimelineMode.SELECT
)


def tool_status_text(mode: TimelineMode) -> str:
    """Persistent status-bar label for the checked timeline tool."""
    for item in TOOL_STRIP_ITEMS:
        if item.mode is mode:
            return f"Tool: {item.label}"
    return "Tool: Select"


# Kept for older call sites / tests that indexed modes.
MODE_VALUES: tuple[str, ...] = tuple(item.mode.value for item in TOOL_STRIP_ITEMS)
MODE_COMBO_LABELS: tuple[str, ...] = tuple(
    f"{item.label} — {item.tip}" for item in TOOL_STRIP_ITEMS
)
