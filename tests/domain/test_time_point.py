from fractions import Fraction

from corrcut.domain.time_point import TimePoint


def test_from_samples_round_trip() -> None:
    tp = TimePoint.from_samples(48000, 48000)
    assert tp.seconds == Fraction(1, 1)
    assert tp.to_samples(48000) == 48000


def test_from_seconds_float_quantizes() -> None:
    tp = TimePoint.from_seconds_float(1.0, 48000)
    assert tp.to_samples(48000) == 48000
    # Sub-sample float noise should snap to nearest sample
    tp2 = TimePoint.from_seconds_float(0.5 + 1e-9, 48000)
    assert tp2.to_samples(48000) == 24000


def test_json_round_trip() -> None:
    original = TimePoint(Fraction(3, 2))
    restored = TimePoint.from_json(original.to_json())
    assert restored.seconds == original.seconds


def test_add_sub() -> None:
    a = TimePoint.from_samples(100, 48000)
    b = TimePoint.from_samples(50, 48000)
    assert (a + b).to_samples(48000) == 150
    assert (a - b).to_samples(48000) == 50
