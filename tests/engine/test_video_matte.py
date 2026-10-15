"""Tests for letterbox / pillarbox matte scanning."""

from __future__ import annotations

import numpy as np

from corrcut.engine.video_matte import (
    PixelCrop,
    choose_stable_crop,
    scan_black_bars,
)


def test_scan_black_bars_finds_letterbox() -> None:
    frame = np.full((108, 192), 180, dtype=np.uint8)
    frame[:14, :] = 0
    frame[94:, :] = 0
    crop = scan_black_bars(frame, limit=24)
    assert crop is not None
    assert crop.y == 14
    assert crop.height == 80
    assert crop.x == 0
    assert crop.width == 192


def test_scan_black_bars_full_frame_returns_none() -> None:
    frame = np.full((108, 192), 120, dtype=np.uint8)
    assert scan_black_bars(frame, limit=24) is None


def test_choose_stable_crop_intersects_samples() -> None:
    crops = [
        PixelCrop(1920, 800, 0, 140),
        PixelCrop(1920, 820, 0, 130),
        PixelCrop(1920, 790, 0, 145),
    ]
    chosen = choose_stable_crop(crops, frame_w=1920, frame_h=1080)
    assert chosen is not None
    assert chosen.x == 0
    assert chosen.y == 144  # snapped even from intersect y0=145
    assert chosen.width == 1920
    assert chosen.height == 790
    box = chosen.as_normalized(1920, 1080)
    assert box["width"] == 1.0
    assert abs(box["y"] - 144 / 1080) < 1e-9


def test_choose_stable_crop_ignores_tiny_dark_scene_blob() -> None:
    """A mostly-black sample must not shrink a real letterbox via intersection."""
    crops = [
        PixelCrop(1920, 810, 0, 135),
        PixelCrop(280, 240, 88, 136),  # dark-scene false positive
        PixelCrop(1920, 800, 0, 140),
    ]
    chosen = choose_stable_crop(crops, frame_w=1920, frame_h=1080)
    assert chosen is not None
    assert chosen.x == 0
    assert chosen.width == 1920
    assert chosen.height >= 800
    assert chosen.height < 1080


def test_choose_stable_crop_rejects_full_frame_only() -> None:
    crops = [
        PixelCrop(1920, 1080, 0, 0),
        PixelCrop(1920, 1078, 0, 1),
    ]
    assert choose_stable_crop(crops, frame_w=1920, frame_h=1080) is None
