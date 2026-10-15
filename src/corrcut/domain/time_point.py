"""Canonical time model — exact rational seconds (see blueprint 5c)."""

from __future__ import annotations

from dataclasses import dataclass
from fractions import Fraction
from typing import Any


@dataclass(frozen=True)
class TimePoint:
    """Exact rational time in seconds; convert to samples/frames only at edges."""

    seconds: Fraction

    def to_samples(self, rate: int) -> int:
        if rate <= 0:
            raise ValueError(f"sample rate must be positive, got {rate}")
        return round(self.seconds * rate)

    def to_frame(self, fps: Fraction) -> int:
        if fps <= 0:
            raise ValueError(f"fps must be positive, got {fps}")
        return int(self.seconds * fps)

    def to_display_ms(self) -> float:
        """Float ms for UI only — never persist this value."""
        return float(self.seconds * 1000)

    @classmethod
    def from_samples(cls, n: int, rate: int) -> TimePoint:
        if rate <= 0:
            raise ValueError(f"sample rate must be positive, got {rate}")
        return cls(Fraction(n, rate))

    @classmethod
    def from_seconds_float(cls, x: float, rate: int) -> TimePoint:
        """Quantize external float timestamps (e.g. Whisper) on ingest."""
        if rate <= 0:
            raise ValueError(f"sample rate must be positive, got {rate}")
        return cls(Fraction(round(x * rate), rate))

    @classmethod
    def from_rational_pair(cls, num: int, den: int) -> TimePoint:
        return cls(Fraction(num, den))

    def to_json(self) -> dict[str, int]:
        return {"num": self.seconds.numerator, "den": self.seconds.denominator}

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> TimePoint:
        if "num" not in data or "den" not in data:
            raise ValueError(f"invalid TimePoint json: {data!r}")
        return cls.from_rational_pair(int(data["num"]), int(data["den"]))

    def __add__(self, other: TimePoint) -> TimePoint:
        return TimePoint(self.seconds + other.seconds)

    def __sub__(self, other: TimePoint) -> TimePoint:
        return TimePoint(self.seconds - other.seconds)
