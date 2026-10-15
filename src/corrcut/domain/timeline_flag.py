"""Point markers on the timeline for later review / conversion into edits."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any

FLAG_SLOTS = (1, 2, 3)
_HEX_COLOR = "0123456789abcdefABCDEF"


def normalize_flag_slot(slot: int) -> int:
    value = int(slot)
    if value not in FLAG_SLOTS:
        raise ValueError(f"flag slot must be one of {FLAG_SLOTS}, got {slot!r}")
    return value


def normalize_flag_color(value: object) -> str | None:
    """Return ``#rrggbb`` or None. Anything else is treated as 'use the slot color'."""
    if not isinstance(value, str):
        return None
    text = value.strip()
    if len(text) == 7 and text[0] == "#" and all(char in _HEX_COLOR for char in text[1:]):
        return "#" + text[1:].lower()
    return None


@dataclass
class TimelineFlag:
    """A colored bookmark at a single timestamp (not a range edit)."""

    id: str
    seconds: float
    slot: int
    color: str | None = None

    @classmethod
    def create(cls, seconds: float, slot: int) -> TimelineFlag:
        return cls(
            id=str(uuid.uuid4()),
            seconds=max(0.0, float(seconds)),
            slot=normalize_flag_slot(slot),
        )

    def to_json(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "id": self.id,
            "seconds": float(self.seconds),
            "slot": int(self.slot),
        }
        color = normalize_flag_color(self.color)
        if color is not None:
            payload["color"] = color
        return payload

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> TimelineFlag:
        return cls(
            id=str(data.get("id") or uuid.uuid4()),
            seconds=max(0.0, float(data.get("seconds", 0.0))),
            slot=normalize_flag_slot(int(data.get("slot", 1))),
            color=normalize_flag_color(data.get("color")),
        )
