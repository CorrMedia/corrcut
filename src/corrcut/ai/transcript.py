"""Transcript word with rational timestamps."""

from __future__ import annotations

from dataclasses import dataclass

from corrcut.domain.time_point import TimePoint


@dataclass(frozen=True)
class TranscriptWord:
    text: str
    start: TimePoint
    end: TimePoint
    probability: float
    segment_text: str
