"""Cached active-picture matte for letterbox-preserving zoom."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from corrcut.engine.video_matte import DEFAULT_CROPDETECT_LIMIT

# Normalized matte covering this much of the frame is treated as "no bars".
TRIVIAL_MATTE_COVERAGE = 0.98
# Letterbox/pillarbox must span nearly one axis and keep enough of the other.
BAR_FULL_AXIS = 0.92
BAR_MIN_CONTENT_AXIS = 0.40
# Bump when the detection algorithm changes so stale caches re-run.
MATTE_DETECTOR = "gray_scan_v3"


@dataclass
class VideoMatteCache:
    """Active picture rect tied to a source fingerprint + detect limit."""

    x: float = 0.0
    y: float = 0.0
    width: float = 1.0
    height: float = 1.0
    limit: float = DEFAULT_CROPDETECT_LIMIT
    fingerprint: dict[str, object] = field(default_factory=dict)
    detector: str = MATTE_DETECTOR

    def as_box(self) -> dict[str, float]:
        return {
            "x": float(self.x),
            "y": float(self.y),
            "width": float(self.width),
            "height": float(self.height),
        }

    def is_trivial(self, *, coverage: float = TRIVIAL_MATTE_COVERAGE) -> bool:
        return float(self.width) >= coverage and float(self.height) >= coverage

    def to_json(self) -> dict[str, Any]:
        return {
            "x": float(self.x),
            "y": float(self.y),
            "width": float(self.width),
            "height": float(self.height),
            "limit": float(self.limit),
            "fingerprint": dict(self.fingerprint),
            "detector": str(self.detector or MATTE_DETECTOR),
        }

    @classmethod
    def from_json(cls, data: dict[str, Any] | None) -> VideoMatteCache | None:
        if not data:
            return None
        try:
            x = float(data.get("x", 0.0))
            y = float(data.get("y", 0.0))
            width = float(data.get("width", 1.0))
            height = float(data.get("height", 1.0))
        except (TypeError, ValueError):
            return None
        if width <= 0 or height <= 0:
            return None
        fp_raw = data.get("fingerprint", {})
        fingerprint = dict(fp_raw) if isinstance(fp_raw, dict) else {}
        try:
            limit = float(data.get("limit", DEFAULT_CROPDETECT_LIMIT))
        except (TypeError, ValueError):
            limit = DEFAULT_CROPDETECT_LIMIT
        detector = str(data.get("detector") or "")
        return cls(
            x=x,
            y=y,
            width=width,
            height=height,
            limit=limit,
            fingerprint=fingerprint,
            detector=detector,
        )

    def matches(
        self,
        fingerprint: dict[str, object],
        *,
        limit: float = DEFAULT_CROPDETECT_LIMIT,
        detector: str = MATTE_DETECTOR,
    ) -> bool:
        from corrcut.domain.relink import fingerprint_matches

        if str(self.detector or "") != str(detector):
            return False
        if abs(float(self.limit) - float(limit)) > 1e-9:
            return False
        return fingerprint_matches(self.fingerprint, fingerprint)


def is_trivial_matte(box: dict[str, float], *, coverage: float = TRIVIAL_MATTE_COVERAGE) -> bool:
    return float(box.get("width", 1.0)) >= coverage and float(box.get("height", 1.0)) >= coverage


def is_plausible_bar_matte(box: dict[str, float]) -> bool:
    """True for letterbox (near-full width) or pillarbox (near-full height).

    Rejects tiny content blobs from dark/noisy sample frames that would paint
    almost the entire preview black and leave a corner tile.
    """
    try:
        w = float(box.get("width", 1.0))
        h = float(box.get("height", 1.0))
    except (TypeError, ValueError):
        return False
    if w <= 0.0 or h <= 0.0:
        return False
    if is_trivial_matte(box):
        return False
    letterbox = w >= BAR_FULL_AXIS and BAR_MIN_CONTENT_AXIS <= h < TRIVIAL_MATTE_COVERAGE
    pillarbox = h >= BAR_FULL_AXIS and BAR_MIN_CONTENT_AXIS <= w < TRIVIAL_MATTE_COVERAGE
    return letterbox or pillarbox
