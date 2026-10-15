"""Regression tests for virtual-scroll waveform peak mapping."""

import numpy as np

from corrcut.ui.timeline.waveform_item import (
    _envelope_segments,
    clip_body_rect,
    column_peak_minmax,
    envelope_path,
    peak_center_x,
    peak_index_range,
    pick_peak_level,
)


def test_peak_index_scales_with_content_x() -> None:
    content_width = 514_535.0
    total = 1_286_338
    start_a, _ = peak_index_range(0.0, content_width, total)
    start_b, _ = peak_index_range(445.0, content_width, total)
    assert start_a == 0
    assert start_b == 1112
    assert start_b > start_a


def test_peak_index_not_time_formula() -> None:
    """Old bug used (content_x / pps) * (total / content_width) — far too small."""
    content_width = 514_535.0
    total = 1_286_338
    pps = 80.0
    content_x = 445.0
    fixed, _ = peak_index_range(content_x, content_width, total)
    wrong = int((content_x / pps) * (total / content_width))
    assert fixed == 1112
    assert wrong == 13


def test_clip_body_follows_scroll_and_inset() -> None:
    rect = clip_body_rect(scroll_x=80.0, pps=100.0, duration=10.0, height=120.0, inset_y=4.0)
    assert rect.left() == -80.0
    assert rect.width() == 1000.0
    assert rect.top() == 4.0
    assert rect.height() == 112.0


def test_column_peak_minmax_aggregates_window() -> None:
    peaks = [(0.0, 0.1), (-0.5, 0.2), (-0.1, 0.8), (0.0, 0.0)]
    pair = column_peak_minmax(0.0, 2.0, peaks)
    assert pair == (-0.5, 0.2)


def test_envelope_path_connects_sample_centers() -> None:
    path = envelope_path([(10.0, 20.0, 80.0), (14.0, 30.0, 70.0)])
    assert not path.isEmpty()
    bounds = path.boundingRect()
    assert bounds.left() == 10.0
    assert bounds.right() == 14.0
    assert bounds.top() == 20.0
    assert bounds.bottom() == 80.0


def test_pick_peak_level_uses_finest_mip_when_zoomed_in() -> None:
    assert pick_peak_level(200, 8, 400.0) == 0
    assert pick_peak_level(200, 8, 200.0) == 0


def test_pick_peak_level_uses_coarser_mip_when_zoomed_out() -> None:
    assert pick_peak_level(200, 8, 50.0) == 2
    assert pick_peak_level(200, 8, 1.0) == 7


def test_envelope_buckets_when_peaks_exceed_pixels() -> None:
    """Zoomed-out progressive paint must not walk every base peak on the UI thread."""
    total = 50_000
    peaks = np.zeros((total, 2), dtype=np.float32)
    peaks[:, 0] = -0.5
    peaks[:, 1] = 0.5
    ready = np.ones(total, dtype=bool)
    segments = _envelope_segments(
        peaks,
        start=0,
        end=total,
        total=total,
        content_width=1000.0,
        scroll_x=0.0,
        mid_y=40.0,
        scale=20.0,
        clip_top=0.0,
        clip_bottom=80.0,
        ready_mask=ready,
        max_points=200,
    )
    assert len(segments) == 1
    assert len(segments[0]) == 200


def test_peak_center_x_is_mid_sample() -> None:
    assert peak_center_x(0, 4, 400.0, 0.0) == 50.0
    assert peak_center_x(1, 4, 400.0, 50.0) == 100.0
