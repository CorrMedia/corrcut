"""Censor params for mute/volume/beep."""

from __future__ import annotations

from fractions import Fraction

from corrcut.domain.operation import (
    CensorParams,
    Operation,
    OperationType,
    default_params,
    normalize_operation_type,
)
from corrcut.domain.time_point import TimePoint


def test_default_params_volume_and_beep() -> None:
    mute = default_params(OperationType.MUTE)
    assert "gain" not in mute
    assert "duck_db" not in mute

    volume = default_params(OperationType.VOLUME)
    assert volume["gain"] == 0.2

    duck = default_params(OperationType.DUCK)
    assert duck["gain"] == 0.2

    beep = default_params(OperationType.BEEP)
    assert beep["beep_freq"] == 1000.0
    assert beep["gain"] == 0.3


def test_operation_from_json_migrates_duck() -> None:
    op = Operation.create(
        OperationType.VOLUME,
        0,
        ["FC"],
        TimePoint(Fraction(0)),
        TimePoint(Fraction(1)),
    )
    raw = op.to_json()
    raw["type"] = "duck"
    raw["params"] = {"duck_db": -20.0}
    loaded = Operation.from_json(raw)
    assert loaded.type == OperationType.VOLUME
    assert abs(float(loaded.params["gain"]) - 0.1) < 1e-6


def test_normalize_aliases() -> None:
    assert normalize_operation_type("punch") == OperationType.ZOOM
    assert normalize_operation_type("boxblur") == OperationType.BLUR
    assert normalize_operation_type("blackout") == OperationType.COVER
    assert normalize_operation_type("mosaic") == OperationType.PIXELATE


def test_censor_params_round_trip() -> None:
    params = CensorParams.defaults().with_gain(0.25).with_beep_freq(880.0)
    volume = params.to_params_dict(OperationType.VOLUME)
    assert volume["gain"] == 0.25
    beep = params.to_params_dict(OperationType.BEEP)
    assert beep["beep_freq"] == 880.0
