"""AI profanity suggestion model (Phase 5)."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from corrcut.domain.operation import TimeRange


class SuggestionStatus(StrEnum):
    PENDING = "pending"
    ACCEPTED = "accepted"
    REJECTED = "rejected"


class DetectionSource(StrEnum):
    WHISPER = "whisper"
    SUBTITLE = "subtitle"
    BOTH = "both"


@dataclass(frozen=True)
class DetectionSettings:
    """Per-run detection options (not persisted on project in v1)."""

    whisper_model: str = "base"
    language: str | None = None
    pre_padding_ms: int = 150
    post_padding_ms: int = 150
    low_confidence_extra_padding_ms: int = 250
    apply_threshold: float = 0.75
    whisper_word_confidence_cutoff: float = 0.55
    use_subtitles: bool = True
    subtitle_stream_index: int | None = None
    subtitle_extra_padding_ms: int = 300
    cross_source_merge_slop_ms: int = 750

    def to_json(self) -> dict[str, Any]:
        return {
            "whisper_model": self.whisper_model,
            "language": self.language,
            "pre_padding_ms": self.pre_padding_ms,
            "post_padding_ms": self.post_padding_ms,
            "low_confidence_extra_padding_ms": self.low_confidence_extra_padding_ms,
            "apply_threshold": self.apply_threshold,
            "whisper_word_confidence_cutoff": self.whisper_word_confidence_cutoff,
            "use_subtitles": self.use_subtitles,
            "subtitle_stream_index": self.subtitle_stream_index,
            "subtitle_extra_padding_ms": self.subtitle_extra_padding_ms,
            "cross_source_merge_slop_ms": self.cross_source_merge_slop_ms,
        }

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> DetectionSettings:
        sub_idx = data.get("subtitle_stream_index")
        return cls(
            whisper_model=str(data.get("whisper_model", "base")),
            language=str(data["language"]) if data.get("language") else None,
            pre_padding_ms=int(data.get("pre_padding_ms", 150)),
            post_padding_ms=int(data.get("post_padding_ms", 150)),
            low_confidence_extra_padding_ms=int(data.get("low_confidence_extra_padding_ms", 250)),
            apply_threshold=float(data.get("apply_threshold", 0.75)),
            whisper_word_confidence_cutoff=float(data.get("whisper_word_confidence_cutoff", 0.55)),
            use_subtitles=bool(data.get("use_subtitles", True)),
            subtitle_stream_index=int(sub_idx) if sub_idx is not None else None,
            subtitle_extra_padding_ms=int(data.get("subtitle_extra_padding_ms", 300)),
            cross_source_merge_slop_ms=int(data.get("cross_source_merge_slop_ms", 750)),
        )


@dataclass
class AiSuggestion:
    id: str
    stream_index: int
    time_range: TimeRange
    matched_word: str
    matched_term: str
    transcript_phrase: str
    confidence: float
    whisper_probability: float
    alignment_quality: str  # "good" | "low"
    category: str = ""
    detection_source: str = DetectionSource.WHISPER.value
    status: SuggestionStatus = SuggestionStatus.PENDING

    @classmethod
    def create(
        cls,
        *,
        stream_index: int,
        time_range: TimeRange,
        matched_word: str,
        matched_term: str,
        transcript_phrase: str,
        confidence: float,
        whisper_probability: float,
        alignment_quality: str,
        category: str = "",
        detection_source: str = DetectionSource.WHISPER.value,
    ) -> AiSuggestion:
        return cls(
            id=str(uuid.uuid4()),
            stream_index=stream_index,
            time_range=time_range,
            matched_word=matched_word,
            matched_term=matched_term,
            transcript_phrase=transcript_phrase,
            confidence=confidence,
            whisper_probability=whisper_probability,
            alignment_quality=alignment_quality,
            category=category,
            detection_source=detection_source,
        )

    def to_json(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "stream_index": self.stream_index,
            "time_range": self.time_range.to_json(),
            "matched_word": self.matched_word,
            "matched_term": self.matched_term,
            "transcript_phrase": self.transcript_phrase,
            "confidence": self.confidence,
            "whisper_probability": self.whisper_probability,
            "alignment_quality": self.alignment_quality,
            "category": self.category,
            "detection_source": self.detection_source,
            "status": self.status.value,
        }

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> AiSuggestion:
        return cls(
            id=str(data["id"]),
            stream_index=int(data["stream_index"]),
            time_range=TimeRange.from_json(data["time_range"]),
            matched_word=str(data["matched_word"]),
            matched_term=str(data["matched_term"]),
            transcript_phrase=str(data["transcript_phrase"]),
            confidence=float(data["confidence"]),
            whisper_probability=float(data["whisper_probability"]),
            alignment_quality=str(data.get("alignment_quality", "good")),
            category=str(data.get("category", "")),
            detection_source=str(data.get("detection_source", DetectionSource.WHISPER.value)),
            status=SuggestionStatus(str(data.get("status", SuggestionStatus.PENDING.value))),
        )
