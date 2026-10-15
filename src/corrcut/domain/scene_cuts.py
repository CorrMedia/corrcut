"""Cached hard scene-cut times for timeline magnetic snap."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from corrcut.engine.scene_cuts import DEFAULT_THRESHOLD


@dataclass
class SceneCutCache:
    """Hard-cut timestamps tied to a source fingerprint + detection threshold."""

    threshold: float = DEFAULT_THRESHOLD
    fingerprint: dict[str, object] = field(default_factory=dict)
    times: list[float] = field(default_factory=list)

    def to_json(self) -> dict[str, Any]:
        return {
            "threshold": float(self.threshold),
            "fingerprint": dict(self.fingerprint),
            "times": [float(t) for t in self.times],
        }

    @classmethod
    def from_json(cls, data: dict[str, Any] | None) -> SceneCutCache | None:
        if not data:
            return None
        raw_times = data.get("times", [])
        times: list[float] = []
        if isinstance(raw_times, list):
            for row in raw_times:
                try:
                    times.append(float(row))
                except (TypeError, ValueError):
                    continue
        fp_raw = data.get("fingerprint", {})
        fingerprint = dict(fp_raw) if isinstance(fp_raw, dict) else {}
        try:
            threshold = float(data.get("threshold", DEFAULT_THRESHOLD))
        except (TypeError, ValueError):
            threshold = DEFAULT_THRESHOLD
        return cls(threshold=threshold, fingerprint=fingerprint, times=times)

    def matches(
        self,
        fingerprint: dict[str, object],
        *,
        threshold: float = DEFAULT_THRESHOLD,
    ) -> bool:
        from corrcut.domain.relink import fingerprint_matches

        if abs(float(self.threshold) - float(threshold)) > 1e-9:
            return False
        return fingerprint_matches(self.fingerprint, fingerprint)
