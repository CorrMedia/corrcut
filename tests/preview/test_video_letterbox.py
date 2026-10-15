"""Tests for mpv video letterbox mapping."""

from __future__ import annotations

from corrcut.preview.mpv_backend import aspect_letterbox_rect, video_letterbox_rect


class _FakeMpv(dict):
    def __getitem__(self, key: str):  # noqa: D105
        return dict.__getitem__(self, key)


class _PropertyMpv:
    """Mimics python-mpv: properties via _get_property, [] is options-only."""

    def __init__(self, props: dict[str, object]) -> None:
        self._props = props

    def _get_property(self, name: str) -> object:
        return self._props[name]

    def __getitem__(self, name: str) -> object:
        raise AssertionError(f"options lookup is not a video property: {name}")


def test_aspect_letterbox_pillarbox() -> None:
    x, y, w, h = aspect_letterbox_rect(1600, 900, 4 / 3)
    assert y == 0.0
    assert h == 900.0
    assert abs(w - 1200.0) < 1e-6
    assert abs(x - 200.0) < 1e-6


def test_aspect_letterbox_letterbox() -> None:
    x, y, w, h = aspect_letterbox_rect(1600, 900, 2.39)
    assert x == 0.0
    assert w == 1600.0
    assert h < 900.0
    assert y > 0.0


def test_letterbox_uses_osd_picture_via_get_property() -> None:
    mpv = _PropertyMpv(
        {
            "osd-dimensions/w": 1920.0,
            "osd-dimensions/h": 1080.0,
            "osd-dimensions/ml": 0.0,
            "osd-dimensions/mr": 0.0,
            "osd-dimensions/mt": 0.0,
            "osd-dimensions/mb": 0.0,
        }
    )
    x, y, w, h = video_letterbox_rect(mpv, 1280.0, 720.0, device_pixel_ratio=1.5)
    assert abs(x - 0.0) < 1e-6
    assert abs(y - 0.0) < 1e-6
    assert abs(w - 1280.0) < 1e-6
    assert abs(h - 720.0) < 1e-6


def test_letterbox_osd_side_bars() -> None:
    mpv = _PropertyMpv(
        {
            "osd-dimensions/w": 1238.0,
            "osd-dimensions/h": 695.0,
            "osd-dimensions/ml": 0.0,
            "osd-dimensions/mr": 0.0,
            "osd-dimensions/mt": 0.0,
            "osd-dimensions/mb": 0.0,
        }
    )
    x, y, w, h = video_letterbox_rect(mpv, 1238.0, 695.0, device_pixel_ratio=1.0)
    assert abs(x - 0.0) < 1e-6
    assert abs(w - 1238.0) < 1e-6
    assert abs(h - 695.0) < 1e-6


def test_letterbox_falls_back_to_display_aspect() -> None:
    mpv = _FakeMpv({"video-params/aspect": 16 / 9})
    x, y, w, h = video_letterbox_rect(mpv, 1238.0, 695.0)
    assert abs((w / h) - (16 / 9)) < 1e-6
    assert abs(h - 695.0) < 1e-6
    assert w > 1043.0  # must not use probe 3:2 (1042.5) into this host
