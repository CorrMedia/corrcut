import json
from fractions import Fraction

from corrcut.domain.operation import SEVERITY_LEVELS, Operation, OperationType, parse_severity
from corrcut.domain.time_point import TimePoint


def test_operation_json_round_trip() -> None:
    op = Operation.create(
        OperationType.MUTE,
        stream_index=1,
        channels=["FC"],
        start=TimePoint(Fraction(1, 1)),
        end=TimePoint(Fraction(2, 1)),
        severity=2,
    )
    raw = json.dumps(op.to_json())
    restored = Operation.from_json(json.loads(raw))
    assert restored.type == OperationType.MUTE
    assert restored.scope.channels == ["FC"]
    assert restored.time_range.start.seconds == Fraction(1, 1)
    assert restored.categories == ["other"]
    assert restored.severity == 2


def test_operation_json_omits_unset_severity() -> None:
    op = Operation.create(
        OperationType.MUTE,
        stream_index=1,
        channels=["FC"],
        start=TimePoint(Fraction(1, 1)),
        end=TimePoint(Fraction(2, 1)),
    )
    assert "severity" not in op.to_json()
    restored = Operation.from_json(op.to_json())
    assert restored.severity is None


def test_parse_severity() -> None:
    assert parse_severity(None) is None
    assert parse_severity(True) is None
    assert parse_severity("unknown") is None
    assert parse_severity(2) == 2
    assert parse_severity(2.0) == 2
    assert parse_severity("3") == 3
    assert parse_severity(1.5) is None
    assert parse_severity("very mild") == 1
    assert parse_severity("mild") == 2
    assert parse_severity("Moderate") == 3
    assert parse_severity("SEVERE") == 4
    assert parse_severity("extreme") == 5
    by_label = {label: number for label, number in SEVERITY_LEVELS}
    assert by_label == {
        "Very mild": 1,
        "Mild": 2,
        "Moderate": 3,
        "Severe": 4,
        "Extreme": 5,
    }
