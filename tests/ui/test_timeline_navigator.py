"""Full-clip overview mapping."""

from PySide6.QtGui import QColor

from corrcut.ui.timeline.timeline_navigator import (
    MIN_HANDLE_WIDTH,
    TimelineNavigator,
    navigator_handle_size,
    navigator_max_scroll,
    navigator_pan_delta,
    playhead_navigator_x,
    scroll_x_for_navigator_x,
)


def test_zoomed_in_handle_keeps_minimum_width() -> None:
    # 2-hour clip, 1s on screen: native thumb would be a sliver.
    width = navigator_handle_size(
        bar_width=400.0,
        duration_s=7200.0,
        viewport_width=400.0,
        pps=400.0,
    )
    assert width == MIN_HANDLE_WIDTH


def test_fitted_handle_fills_the_bar() -> None:
    width = navigator_handle_size(
        bar_width=400.0,
        duration_s=10.0,
        viewport_width=400.0,
        pps=40.0,
    )
    assert width == 400.0


def test_click_at_end_scrolls_to_max() -> None:
    scroll = scroll_x_for_navigator_x(
        x=400.0,
        bar_width=400.0,
        duration_s=120.0,
        viewport_width=400.0,
        pps=80.0,
    )
    assert scroll == navigator_max_scroll(120.0, 80.0, 400.0)


def test_click_at_start_scrolls_to_zero() -> None:
    assert (
        scroll_x_for_navigator_x(
            x=0.0,
            bar_width=400.0,
            duration_s=120.0,
            viewport_width=400.0,
            pps=80.0,
        )
        == 0.0
    )


def test_playhead_x_tracks_duration_fraction() -> None:
    assert playhead_navigator_x(30.0, 120.0, 400.0) == 100.0


def test_zoomed_in_pan_is_much_less_sensitive_than_full_clip_mapping() -> None:
    handle = navigator_handle_size(
        bar_width=400.0,
        duration_s=7200.0,
        viewport_width=400.0,
        pps=400.0,
    )
    pan = navigator_pan_delta(1.0, viewport_width=400.0, handle_width=handle)
    full_clip = navigator_max_scroll(7200.0, 400.0, 400.0) / (400.0 - handle)
    assert pan == 400.0 / MIN_HANDLE_WIDTH
    assert pan < full_clip / 100.0


def test_natural_handle_pan_matches_full_clip_mapping() -> None:
    # Half the clip visible: handle is half the bar, no min-size floor.
    handle = navigator_handle_size(
        bar_width=400.0,
        duration_s=10.0,
        viewport_width=400.0,
        pps=80.0,
    )
    assert handle == 200.0
    pan = navigator_pan_delta(1.0, viewport_width=400.0, handle_width=handle)
    assert pan == 2.0


def test_direct_pan_matches_a_normal_scrollbar_when_zoomed_in() -> None:
    handle = navigator_handle_size(
        bar_width=400.0,
        duration_s=7200.0,
        viewport_width=400.0,
        pps=400.0,
    )
    max_scroll = navigator_max_scroll(7200.0, 400.0, 400.0)
    pan = navigator_pan_delta(
        1.0,
        viewport_width=400.0,
        handle_width=handle,
        bar_width=400.0,
        max_scroll=max_scroll,
        direct=True,
    )
    assert pan == max_scroll / (400.0 - handle)


def test_natural_handle_direct_pan_matches_default() -> None:
    handle = 200.0
    max_scroll = navigator_max_scroll(10.0, 80.0, 400.0)
    direct = navigator_pan_delta(
        1.0,
        viewport_width=400.0,
        handle_width=handle,
        bar_width=400.0,
        max_scroll=max_scroll,
        direct=True,
    )
    default = navigator_pan_delta(1.0, viewport_width=400.0, handle_width=handle)
    assert direct == default == 2.0


class _CountingNavigator(TimelineNavigator):
    def __init__(self) -> None:
        super().__init__()
        self.updates = 0

    def update(self, *args: object) -> None:  # noqa: ANN002
        self.updates += 1
        super().update(*args)


def test_set_flags_skips_repaint_when_unchanged() -> None:
    nav = _CountingNavigator()
    red = QColor(10, 20, 30)
    nav.set_flags([(1.5, red)])
    assert nav.updates == 1

    nav.set_flags([(1.5, QColor(10, 20, 30))])
    assert nav.updates == 1

    nav.set_flags([(1.5, QColor(10, 20, 31))])
    assert nav.updates == 2

    nav.set_flags([(2.0, QColor(10, 20, 31))])
    assert nav.updates == 3

    nav.set_flags([])
    assert nav.updates == 4
    nav.set_flags([])
    assert nav.updates == 4
