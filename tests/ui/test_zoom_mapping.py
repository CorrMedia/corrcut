from corrcut.ui.timeline.zoom_mapping import (
    MAX_PIXELS_PER_SECOND,
    MIN_PIXELS_PER_SECOND,
    max_pixels_per_second,
    pixels_per_second_from_slider,
    slider_from_pixels_per_second,
)


def test_slider_roundtrip_at_extremes() -> None:
    assert pixels_per_second_from_slider(0) == MIN_PIXELS_PER_SECOND
    assert pixels_per_second_from_slider(1000) == max_pixels_per_second()


def test_viewport_caps_max_zoom() -> None:
    assert max_pixels_per_second(1200.0) == 400.0
    assert max_pixels_per_second(300.0) == 300.0


def test_slider_roundtrip_mid() -> None:
    pps = 80.0
    s = slider_from_pixels_per_second(pps)
    assert abs(pixels_per_second_from_slider(s) - pps) < 2.0
