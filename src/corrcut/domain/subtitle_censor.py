"""Backward-compatible re-exports (use subtitle_settings.py)."""

from corrcut.domain.subtitle_settings import (
    DEFAULT_EDITED_TRACK_NAME as DEFAULT_SUBTITLE_TRACK_NAME,
)
from corrcut.domain.subtitle_settings import (
    SubtitleSettings as SubtitleCensorSettings,
)

__all__ = [
    "DEFAULT_SUBTITLE_TRACK_NAME",
    "SubtitleCensorSettings",
]
