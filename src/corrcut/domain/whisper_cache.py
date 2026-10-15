"""Cached Whisper transcript words for subtitle generation."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from corrcut.domain.time_point import TimePoint

if TYPE_CHECKING:
    from corrcut.ai.transcript import TranscriptWord


@dataclass
class WhisperCache:
    stream_index: int
    model: str
    words: list[dict[str, Any]]

    def to_json(self) -> dict[str, Any]:
        return {
            "stream_index": self.stream_index,
            "model": self.model,
            "words": self.words,
        }

    @classmethod
    def from_json(cls, data: dict[str, Any] | None) -> WhisperCache | None:
        if not data:
            return None
        words_raw = data.get("words", [])
        words = [dict(row) for row in words_raw if isinstance(row, dict)]
        return cls(
            stream_index=int(data["stream_index"]),
            model=str(data.get("model", "base")),
            words=words,
        )

    @classmethod
    def from_transcript_words(
        cls,
        *,
        stream_index: int,
        model: str,
        words: list[TranscriptWord],
    ) -> WhisperCache:
        return cls(
            stream_index=stream_index,
            model=model,
            words=[
                {
                    "text": word.text,
                    "start": word.start.to_json(),
                    "end": word.end.to_json(),
                    "probability": word.probability,
                    "segment_text": word.segment_text,
                }
                for word in words
            ],
        )

    def transcript_words(self) -> list[TranscriptWord]:
        from corrcut.ai.transcript import TranscriptWord

        rows: list[TranscriptWord] = []
        for row in self.words:
            rows.append(
                TranscriptWord(
                    text=str(row["text"]),
                    start=TimePoint.from_json(row["start"]),
                    end=TimePoint.from_json(row["end"]),
                    probability=float(row.get("probability", 0.5)),
                    segment_text=str(row.get("segment_text", row["text"])),
                )
            )
        return rows
