"""Project subtitle cue model (Phase 7)."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from enum import Enum
from fractions import Fraction
from typing import Any

from corrcut.domain.time_point import TimePoint


class SubtitleSource(str, Enum):
    EMBEDDED = "embedded"
    WHISPER = "whisper"
    OCR = "ocr"
    USER = "user"


@dataclass
class SubtitleCue:
    """Canonical subtitle cue stored on the project file."""

    id: str
    start: TimePoint
    end: TimePoint
    text: str
    edited_text: str
    source: SubtitleSource
    user_edited: bool = False

    @classmethod
    def create(
        cls,
        *,
        start: TimePoint,
        end: TimePoint,
        text: str,
        edited_text: str | None = None,
        source: SubtitleSource = SubtitleSource.EMBEDDED,
        user_edited: bool = False,
        cue_id: str | None = None,
    ) -> SubtitleCue:
        return cls(
            id=cue_id or str(uuid.uuid4()),
            start=start,
            end=end,
            text=text,
            edited_text=edited_text if edited_text is not None else text,
            source=source,
            user_edited=user_edited,
        )

    def duration_seconds(self) -> Fraction:
        return self.end.seconds - self.start.seconds

    def to_json(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "start": self.start.to_json(),
            "end": self.end.to_json(),
            "text": self.text,
            "edited_text": self.edited_text,
            "source": self.source.value,
            "user_edited": self.user_edited,
        }

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> SubtitleCue:
        return cls(
            id=str(data["id"]),
            start=TimePoint.from_json(data["start"]),
            end=TimePoint.from_json(data["end"]),
            text=str(data.get("text", "")),
            edited_text=str(data.get("edited_text", data.get("text", ""))),
            source=SubtitleSource(str(data.get("source", SubtitleSource.EMBEDDED.value))),
            user_edited=bool(data.get("user_edited", False)),
        )
