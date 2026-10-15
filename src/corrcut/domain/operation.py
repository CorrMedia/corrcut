"""Canonical Operation model for corr.json authoring."""

from __future__ import annotations

import math
import uuid
from dataclasses import dataclass, field, replace
from enum import StrEnum
from fractions import Fraction
from typing import Any

from corrcut.domain.corr_categories import OTHER_ID, resolve_categories
from corrcut.domain.time_point import TimePoint

# Item-level edits (skip + video) are not bound to an audio stream.
ITEM_LEVEL_STREAM_INDEX = -1

DEFAULT_VOLUME_GAIN = 0.2
DEFAULT_BEEP_GAIN = 0.3
DEFAULT_BEEP_FREQ = 1000.0
DEFAULT_ZOOM_SCALE = 2.0
DEFAULT_BLUR_RADIUS = 8.0
DEFAULT_PIXEL_SIZE = 16.0
DEFAULT_BOX = {"x": 0.35, "y": 0.35, "width": 0.3, "height": 0.3}

_AUDIO_PARAM_KEYS = frozenset({"gain", "beep_freq", "frequency", "duck_db", "fade_in", "fade_out"})

BEEP_PRESETS: list[tuple[str, float]] = [
    ("TV standard (1000 Hz)", 1000.0),
    ("High (1500 Hz)", 1500.0),
    ("Medium (880 Hz)", 880.0),
    ("Low (800 Hz)", 800.0),
]


class OperationType(StrEnum):
    MUTE = "mute"
    VOLUME = "volume"
    BEEP = "beep"
    SKIP = "skip"
    BLANK = "blank"
    COVER = "cover"
    BLUR = "blur"
    PIXELATE = "pixelate"
    ZOOM = "zoom"
    CROP = "crop"
    # Legacy stored values — normalized on load.
    DUCK = "duck"
    GAIN = "gain"


_AUDIO_TYPES = frozenset({OperationType.MUTE, OperationType.VOLUME, OperationType.BEEP})
_ITEM_LEVEL_TYPES = frozenset(
    {
        OperationType.SKIP,
        OperationType.BLANK,
        OperationType.COVER,
        OperationType.BLUR,
        OperationType.PIXELATE,
        OperationType.ZOOM,
        OperationType.CROP,
    }
)
_SPATIAL_TYPES = frozenset(
    {
        OperationType.COVER,
        OperationType.BLUR,
        OperationType.PIXELATE,
        OperationType.ZOOM,
        OperationType.CROP,
    }
)
_BOX_DEFAULT_TYPES = frozenset({OperationType.COVER, OperationType.BLUR, OperationType.PIXELATE})


def is_audio_operation(op_type: OperationType) -> bool:
    return op_type in _AUDIO_TYPES


def is_item_level_operation(op_type: OperationType) -> bool:
    return op_type in _ITEM_LEVEL_TYPES


def is_spatial_operation(op_type: OperationType) -> bool:
    return op_type in _SPATIAL_TYPES


def normalize_operation_type(raw: str | OperationType) -> OperationType:
    value = raw.value if isinstance(raw, OperationType) else str(raw).strip().lower()
    if value in {"duck", "gain"}:
        return OperationType.VOLUME
    if value == "punch":
        return OperationType.ZOOM
    if value == "boxblur":
        return OperationType.BLUR
    if value == "blackout":
        return OperationType.COVER
    if value == "mosaic":
        return OperationType.PIXELATE
    return OperationType(value)


def duck_db_to_gain(duck_db: float) -> float:
    return float(max(0.0, min(1.0, 10 ** (float(duck_db) / 20.0))))


def gain_to_duck_db(gain: float) -> float:
    g = max(1e-6, min(1.0, float(gain)))
    return 20.0 * math.log10(g)


SEVERITY_LEVELS: tuple[tuple[str, int], ...] = (
    ("Very mild", 1),
    ("Mild", 2),
    ("Moderate", 3),
    ("Severe", 4),
    ("Extreme", 5),
)
_SEVERITY_BY_TOKEN: dict[str, int] = {label.lower(): number for label, number in SEVERITY_LEVELS}


def parse_severity(raw: Any) -> int | None:
    """Optional corr.json ``edits[].severity``. Integer 1–5 or a severity term."""
    if raw is None or isinstance(raw, bool):
        return None
    if isinstance(raw, int):
        return raw
    if isinstance(raw, float) and raw.is_integer():
        return int(raw)
    if isinstance(raw, str) and raw.strip():
        token = raw.strip()
        mapped = _SEVERITY_BY_TOKEN.get(token.lower())
        if mapped is not None:
            return mapped
        try:
            number = float(token)
        except ValueError:
            return None
        if number.is_integer():
            return int(number)
    return None


@dataclass
class TimeRange:
    start: TimePoint
    end: TimePoint

    def to_json(self) -> dict[str, Any]:
        return {"start": self.start.to_json(), "end": self.end.to_json()}

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> TimeRange:
        return cls(
            start=TimePoint.from_json(data["start"]),
            end=TimePoint.from_json(data["end"]),
        )

    def duration_seconds(self) -> Fraction:
        return self.end.seconds - self.start.seconds


@dataclass
class OperationScope:
    stream_index: int
    channels: list[str]

    def to_json(self) -> dict[str, Any]:
        return {"stream_index": self.stream_index, "channels": list(self.channels)}

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> OperationScope:
        return cls(
            stream_index=int(data["stream_index"]),
            channels=list(data.get("channels", [])),
        )


@dataclass
class Operation:
    id: str
    type: OperationType
    scope: OperationScope
    time_range: TimeRange
    params: dict[str, Any] = field(default_factory=dict)
    order: int = 0
    enabled: bool = True
    source: str = "user"
    categories: list[str] = field(default_factory=lambda: [OTHER_ID])
    description: str | None = None
    severity: int | None = None
    language: str | None = None

    @classmethod
    def create(
        cls,
        op_type: OperationType,
        stream_index: int,
        channels: list[str],
        start: TimePoint,
        end: TimePoint,
        *,
        params: dict[str, Any] | None = None,
        order: int = 0,
        source: str = "user",
        categories: list[str] | None = None,
        description: str | None = None,
        severity: int | None = None,
        language: str | None = None,
    ) -> Operation:
        op_type = normalize_operation_type(op_type)
        if is_item_level_operation(op_type):
            stream_index = ITEM_LEVEL_STREAM_INDEX
            channels = []
        return cls(
            id=str(uuid.uuid4()),
            type=op_type,
            scope=OperationScope(stream_index=stream_index, channels=channels),
            time_range=TimeRange(start=start, end=end),
            params=params or default_params(op_type),
            order=order,
            source=source,
            categories=resolve_categories(categories),
            description=description,
            severity=severity,
            language=None if is_item_level_operation(op_type) else language,
        )

    @property
    def is_item_level(self) -> bool:
        return self.scope.stream_index == ITEM_LEVEL_STREAM_INDEX or is_item_level_operation(
            self.type
        )

    def to_json(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "id": self.id,
            "type": self.type.value,
            "scope": self.scope.to_json(),
            "time_range": self.time_range.to_json(),
            "params": self.params,
            "order": self.order,
            "enabled": self.enabled,
            "source": self.source,
            "categories": list(self.categories),
        }
        if self.description:
            data["description"] = self.description
        if self.severity is not None:
            data["severity"] = self.severity
        if self.language:
            data["language"] = self.language
        return data

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> Operation:
        op_type = normalize_operation_type(str(data["type"]))
        params = _migrate_params(op_type, dict(data.get("params", {})))
        scope = OperationScope.from_json(data["scope"])
        if is_item_level_operation(op_type):
            scope = OperationScope(stream_index=ITEM_LEVEL_STREAM_INDEX, channels=[])
        description = data.get("description")
        language = data.get("language")
        return cls(
            id=str(data["id"]),
            type=op_type,
            scope=scope,
            time_range=TimeRange.from_json(data["time_range"]),
            params=params,
            order=int(data.get("order", 0)),
            enabled=bool(data.get("enabled", True)),
            source=str(data.get("source", "user")),
            categories=resolve_categories(
                [str(c) for c in data.get("categories", []) if str(c).strip()]
            ),
            description=str(description).strip() if description else None,
            severity=parse_severity(data.get("severity")),
            language=str(language).strip() if language else None,
        )

    def censor_params(self) -> CensorParams:
        return CensorParams.from_params(self.params)

    def apply_censor_params(self, params: CensorParams) -> None:
        extra = {k: v for k, v in self.params.items() if k not in _AUDIO_PARAM_KEYS}
        self.params = params.to_params_dict(self.type, preserve_extra=extra)


@dataclass(frozen=True)
class CensorParams:
    """Typed view of mute/volume/beep operation params (preview audio)."""

    gain: float = DEFAULT_VOLUME_GAIN
    beep_freq: float = DEFAULT_BEEP_FREQ
    beep_gain: float = DEFAULT_BEEP_GAIN

    @classmethod
    def defaults(cls) -> CensorParams:
        return cls()

    @classmethod
    def from_params(cls, params: dict[str, Any]) -> CensorParams:
        gain = _gain_from_params(params)
        freq = float(params.get("beep_freq", params.get("frequency", DEFAULT_BEEP_FREQ)))
        beep_gain = float(params.get("beep_gain", DEFAULT_BEEP_GAIN))
        if "gain" in params and "beep_freq" in params:
            beep_gain = float(params.get("gain", beep_gain))
        return cls(gain=gain, beep_freq=freq, beep_gain=beep_gain)

    @property
    def duck_db(self) -> float:
        """Legacy view of volume gain as dB (for old UI math)."""
        return gain_to_duck_db(self.gain)

    def with_gain(self, gain: float) -> CensorParams:
        return replace(self, gain=max(0.0, min(1.0, float(gain))))

    def with_duck_db(self, duck_db: float) -> CensorParams:
        return self.with_gain(duck_db_to_gain(duck_db))

    def with_beep_freq(self, beep_freq: float) -> CensorParams:
        return replace(self, beep_freq=float(beep_freq))

    def with_beep_gain(self, beep_gain: float) -> CensorParams:
        return replace(self, beep_gain=max(0.0, min(1.0, float(beep_gain))))

    # Fade APIs kept as no-ops so existing call sites compile during retarget.
    def fade_in_ms(self) -> int:
        return 0

    def fade_out_ms(self) -> int:
        return 0

    def with_fade_ms(self, fade_in_ms: int, fade_out_ms: int) -> CensorParams:
        return self

    def clamped_fade_ms(self, duration_s: float) -> CensorParams:
        return self

    def to_params_dict(
        self,
        op_type: OperationType,
        *,
        preserve_extra: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        result: dict[str, Any] = {}
        if op_type == OperationType.VOLUME:
            result["gain"] = self.gain
        elif op_type == OperationType.BEEP:
            result["beep_freq"] = self.beep_freq
            result["gain"] = self.beep_gain
        if preserve_extra:
            for key, value in preserve_extra.items():
                if key not in _AUDIO_PARAM_KEYS:
                    result[key] = value
        return result


def _gain_from_params(params: dict[str, Any]) -> float:
    if "gain" in params and "beep_freq" not in params and "frequency" not in params:
        return max(0.0, min(1.0, float(params["gain"])))
    if "duck_db" in params:
        return duck_db_to_gain(float(params["duck_db"]))
    if "gain" in params:
        return max(0.0, min(1.0, float(params["gain"])))
    return DEFAULT_VOLUME_GAIN


def _migrate_params(op_type: OperationType, params: dict[str, Any]) -> dict[str, Any]:
    cleaned = dict(params)
    cleaned.pop("fade_in", None)
    cleaned.pop("fade_out", None)
    if op_type == OperationType.VOLUME:
        if "gain" not in cleaned and "duck_db" in cleaned:
            cleaned["gain"] = duck_db_to_gain(float(cleaned.pop("duck_db")))
        else:
            cleaned.pop("duck_db", None)
            if "gain" not in cleaned:
                cleaned["gain"] = DEFAULT_VOLUME_GAIN
    elif op_type == OperationType.BEEP:
        if "frequency" in cleaned and "beep_freq" not in cleaned:
            cleaned["beep_freq"] = float(cleaned.pop("frequency"))
        cleaned.setdefault("beep_freq", DEFAULT_BEEP_FREQ)
        cleaned.setdefault("gain", DEFAULT_BEEP_GAIN)
        cleaned.pop("duck_db", None)
    elif op_type == OperationType.MUTE:
        cleaned.pop("duck_db", None)
        cleaned.pop("gain", None)
        cleaned.pop("beep_freq", None)
    return cleaned


def default_params(op_type: OperationType) -> dict[str, Any]:
    op_type = normalize_operation_type(op_type)
    if op_type == OperationType.VOLUME:
        return {"gain": DEFAULT_VOLUME_GAIN}
    if op_type == OperationType.BEEP:
        return {"beep_freq": DEFAULT_BEEP_FREQ, "gain": DEFAULT_BEEP_GAIN}
    if op_type == OperationType.BLUR:
        return {"radius": DEFAULT_BLUR_RADIUS, "box": dict(DEFAULT_BOX)}
    if op_type == OperationType.PIXELATE:
        return {"size": DEFAULT_PIXEL_SIZE, "box": dict(DEFAULT_BOX)}
    if op_type == OperationType.COVER:
        return {"box": dict(DEFAULT_BOX)}
    if op_type in {OperationType.ZOOM, OperationType.CROP}:
        return {"scale": DEFAULT_ZOOM_SCALE, "x": 0.5, "y": 0.5}
    return {}


def clamp_fade_ms(fade_in_ms: int, fade_out_ms: int, duration_s: float) -> tuple[int, int]:
    """Legacy helper — fades are not authored for corr.json."""
    return 0, 0


def beep_preset_index_for_freq(freq: float) -> int:
    for index, (_, preset_hz) in enumerate(BEEP_PRESETS):
        if abs(preset_hz - freq) < 0.5:
            return index
    return -1


def fade_seconds(params: dict[str, Any], key: str) -> Fraction:
    return Fraction(0)


def fade_milliseconds(params: dict[str, Any], key: str) -> int:
    return 0


def set_fade_milliseconds(params: dict[str, Any], key: str, ms: int) -> None:
    params.pop(key, None)
