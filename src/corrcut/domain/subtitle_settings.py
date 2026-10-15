"""Per-project subtitle workflow settings (Phase 7)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

DEFAULT_GENERATED_TRACK_NAME = "Generated"
DEFAULT_EDITED_TRACK_NAME = "Edited"


@dataclass
class SubtitleSettings:
    """Persisted subtitle ingest, preview, and export options."""

    stream_index: int | None = None
    bitmap_stream_index: int | None = None
    append_generated: bool = False
    append_edited: bool = True
    replace_existing_generated_track: bool = True
    replace_existing_edited_track: bool = True
    track_name: str = DEFAULT_EDITED_TRACK_NAME

    def to_json(self) -> dict[str, Any]:
        return {
            "stream_index": self.stream_index,
            "bitmap_stream_index": self.bitmap_stream_index,
            "append_generated": self.append_generated,
            "append_edited": self.append_edited,
            "replace_existing_generated_track": self.replace_existing_generated_track,
            "replace_existing_edited_track": self.replace_existing_edited_track,
            "track_name": self.track_name,
        }

    @classmethod
    def from_json(cls, data: dict[str, Any] | None) -> SubtitleSettings:
        if not data:
            return cls()
        stream_index = data.get("stream_index")
        bitmap_stream_index = data.get("bitmap_stream_index")
        return cls(
            stream_index=int(stream_index) if stream_index is not None else None,
            bitmap_stream_index=(
                int(bitmap_stream_index) if bitmap_stream_index is not None else None
            ),
            append_generated=bool(data.get("append_generated", False)),
            append_edited=bool(
                data.get("append_edited", data.get("enabled", True))
            ),
            replace_existing_generated_track=bool(
                data.get("replace_existing_generated_track", True)
            ),
            replace_existing_edited_track=bool(
                data.get(
                    "replace_existing_edited_track",
                    data.get("replace_existing_named_track", True),
                )
            ),
            track_name=str(data.get("track_name", DEFAULT_EDITED_TRACK_NAME)),
        )
