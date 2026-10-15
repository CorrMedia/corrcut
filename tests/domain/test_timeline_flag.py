import pytest

from corrcut.domain.timeline_flag import (
    TimelineFlag,
    normalize_flag_color,
    normalize_flag_slot,
)


def test_create_flag_assigns_id_and_slot() -> None:
    flag = TimelineFlag.create(12.5, 2)
    assert flag.seconds == 12.5
    assert flag.slot == 2
    assert flag.id


def test_create_flag_clamps_negative_time() -> None:
    assert TimelineFlag.create(-3.0, 1).seconds == 0.0


def test_normalize_flag_slot_rejects_unknown() -> None:
    with pytest.raises(ValueError):
        normalize_flag_slot(4)


def test_flag_json_round_trip() -> None:
    flag = TimelineFlag.create(1.25, 3)
    loaded = TimelineFlag.from_json(flag.to_json())
    assert loaded.id == flag.id
    assert loaded.seconds == flag.seconds
    assert loaded.slot == flag.slot
    assert loaded.color is None
    assert "color" not in flag.to_json()


def test_flag_custom_color_round_trip() -> None:
    flag = TimelineFlag.create(1.25, 1)
    flag.color = "#00AA00"
    payload = flag.to_json()
    assert payload["color"] == "#00aa00"
    loaded = TimelineFlag.from_json(payload)
    assert loaded.color == "#00aa00"
    assert loaded.slot == 1


def test_flag_ignores_invalid_color() -> None:
    loaded = TimelineFlag.from_json({"seconds": 2.0, "slot": 2, "color": "green"})
    assert loaded.color is None
    assert normalize_flag_color(None) is None
    assert normalize_flag_color("#abc") is None
