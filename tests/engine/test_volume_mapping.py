"""Linear-amplitude mapping for mpv's cubic soft-volume property."""

from corrcut.preview.mpv_backend import (
    linear_percent_to_mpv_volume,
    mpv_volume_to_linear_percent,
)


def test_linear_half_maps_above_mpv_midpoint() -> None:
    """mpv vol 50 ≈ 12.5% amplitude; linear 50% needs a higher mpv value."""
    assert round(linear_percent_to_mpv_volume(50), 1) == 79.4


def test_mpv_midpoint_maps_below_linear_half() -> None:
    assert mpv_volume_to_linear_percent(50) == 12


def test_linear_roundtrip() -> None:
    for linear in (0, 1, 25, 50, 75, 100):
        mpv = linear_percent_to_mpv_volume(linear)
        assert mpv_volume_to_linear_percent(mpv) == linear


def test_clamps_linear_input() -> None:
    assert linear_percent_to_mpv_volume(-5) == 0.0
    assert linear_percent_to_mpv_volume(150) == 100.0
