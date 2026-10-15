"""Unit tests for time-ruler tick intervals and labels."""

from corrcut.ui.timeline.time_ruler import (
    format_ruler_label,
    major_interval_for_pps,
    minor_interval_for_major,
    visible_scene_cuts,
)


def test_major_interval_grows_as_pps_shrinks() -> None:
    fine = major_interval_for_pps(400.0)
    mid = major_interval_for_pps(80.0)
    coarse = major_interval_for_pps(2.0)
    assert fine <= mid <= coarse
    assert fine * 400.0 >= 80.0
    assert mid * 80.0 >= 80.0


def test_major_interval_at_common_zooms() -> None:
    assert major_interval_for_pps(100.0) == 1.0  # 100px per major
    assert major_interval_for_pps(20.0) == 5.0
    assert major_interval_for_pps(1.0) == 120.0 or major_interval_for_pps(1.0) >= 60.0


def test_minor_is_one_fifth_of_major() -> None:
    assert minor_interval_for_major(1.0) == 0.2
    assert minor_interval_for_major(10.0) == 2.0
    assert minor_interval_for_major(0.5) == 0.1


def test_format_ruler_label_under_hour() -> None:
    assert format_ruler_label(0.0) == "00:00:00.00"
    assert format_ruler_label(12.0) == "00:00:12.00"
    assert format_ruler_label(90.0) == "00:01:30.00"
    assert format_ruler_label(3599.0) == "00:59:59.00"


def test_format_ruler_label_at_and_over_hour() -> None:
    assert format_ruler_label(3600.0) == "01:00:00.00"
    assert format_ruler_label(3661.0) == "01:01:01.00"


def test_format_ruler_label_centiseconds() -> None:
    assert format_ruler_label(12.5) == "00:00:12.50"
    assert format_ruler_label(0.1) == "00:00:00.10"


def test_visible_scene_cuts_binary_searches_the_viewport() -> None:
    # pps=100, scroll shows 10s–18s, plus the 2px overscan (0.02s).
    cuts = tuple(float(i) for i in range(5000))
    visible = visible_scene_cuts(
        cuts,
        scroll_x=1000.0,
        viewport_width=800.0,
        pps=100.0,
    )
    assert list(visible) == [float(t) for t in range(10, 19)]


def test_visible_scene_cuts_includes_overscan_edges() -> None:
    # left = (0 - 2) / 100 = -0.02; right = (100 + 2) / 100 = 1.02
    cuts = (-1.0, -0.02, 0.0, 0.5, 1.02, 1.03, 50.0)
    visible = visible_scene_cuts(
        cuts,
        scroll_x=0.0,
        viewport_width=100.0,
        pps=100.0,
    )
    assert list(visible) == [-0.02, 0.0, 0.5, 1.02]


def test_visible_scene_cuts_empty_when_none_on_screen() -> None:
    cuts = (0.0, 1.0, 2.0)
    assert (
        visible_scene_cuts(cuts, scroll_x=10_000.0, viewport_width=800.0, pps=100.0) == ()
    )
    assert visible_scene_cuts((), scroll_x=0.0, viewport_width=800.0, pps=100.0) == ()
