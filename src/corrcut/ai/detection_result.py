"""AI detection output including optional Whisper words for subtitle cache."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from corrcut.ai.transcript import TranscriptWord
from corrcut.domain.ai_suggestion import AiSuggestion
from corrcut.domain.time_point import TimePoint


@dataclass(frozen=True)
class DetectionResult:
    suggestions: list[AiSuggestion]
    whisper_words: tuple[TranscriptWord, ...] = ()

    def whisper_words_to_json(self) -> list[dict[str, Any]]:
        return [
            {
                "text": word.text,
                "start": word.start.to_json(),
                "end": word.end.to_json(),
                "probability": word.probability,
                "segment_text": word.segment_text,
            }
            for word in self.whisper_words
        ]

    @classmethod
    def whisper_words_from_json(
        cls,
        rows: list[dict[str, Any]] | None,
    ) -> tuple[TranscriptWord, ...]:
        if not rows:
            return ()
        words: list[TranscriptWord] = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            words.append(
                TranscriptWord(
                    text=str(row["text"]),
                    start=TimePoint.from_json(row["start"]),
                    end=TimePoint.from_json(row["end"]),
                    probability=float(row.get("probability", 0.5)),
                    segment_text=str(row.get("segment_text", row["text"])),
                )
            )
        return tuple(words)
