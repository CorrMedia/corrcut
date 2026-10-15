"""Regression tests for mpv lavfi-complex wiring (aid label rewrite, passthrough)."""

from __future__ import annotations

from typing import Any

import pytest
from PySide6.QtWidgets import QApplication

from corrcut.preview.lavfi_mpv import MPV_PASSTHROUGH
from corrcut.preview.mpv_backend import MpvPlayerBackend

# Ensure a Qt application exists for QObject construction.
_app = QApplication.instance() or QApplication([])


class _FakeMpv:
    """Minimal mpv stand-in for set_lavfi_complex / set_audio_stream tests."""

    def __init__(
        self,
        *,
        track_list: list[dict[str, Any]] | None = None,
        aid: Any = "no",
    ) -> None:
        self.track_list = track_list or [
            {"id": 1, "type": "audio", "demux_id": None},
            {"id": 2, "type": "audio", "demux_id": None},
        ]
        self.aid = aid
        self.volume = 100
        self.mute = False
        self.lavfi_complex = ""

    def __setattr__(self, name: str, value: Any) -> None:
        object.__setattr__(self, name, value)


@pytest.fixture
def backend() -> MpvPlayerBackend:
    player = MpvPlayerBackend()
    fake = _FakeMpv()
    player._mpv = fake  # noqa: SLF001 — test injects fake mpv
    player.ingest_track_list(fake.track_list)
    return player


def test_set_lavfi_complex_rewrites_aid_with_selected_track_id(backend: MpvPlayerBackend) -> None:
    backend._selected_aid = 1  # noqa: SLF001
    backend.set_lavfi_complex("[aid]volume=0.5[ao]")
    assert backend._mpv.lavfi_complex == "[aid1]volume=0.5[ao]"  # type: ignore[union-attr]


def test_set_lavfi_complex_passthrough_rewrites_to_track_suffixed_pad(
    backend: MpvPlayerBackend,
) -> None:
    backend._selected_aid = 2  # noqa: SLF001
    backend._selected_vid = 1  # noqa: SLF001
    backend.set_lavfi_complex(MPV_PASSTHROUGH)
    assert backend._mpv.lavfi_complex == "[vid1]null[vo];[aid2]anull[ao]"  # type: ignore[union-attr]


def test_set_lavfi_complex_ignores_mpv_aid_false_when_selected_aid_set(
    backend: MpvPlayerBackend,
) -> None:
    """Once lavfi is active mpv reports aid=False; must not produce [aid0]."""
    backend._selected_aid = 1  # noqa: SLF001
    backend._mpv.aid = False  # type: ignore[union-attr]
    backend.set_lavfi_complex("[aid]anull[ao]")
    assert backend._mpv.lavfi_complex == "[aid1]anull[ao]"  # type: ignore[union-attr]


def test_set_lavfi_complex_uses_live_aid_when_selected_aid_unset(
    backend: MpvPlayerBackend,
) -> None:
    backend._selected_aid = None  # noqa: SLF001
    backend._mpv.aid = 2  # type: ignore[union-attr]
    backend.set_lavfi_complex("[aid]anull[ao]")
    assert backend._mpv.lavfi_complex == "[aid2]anull[ao]"  # type: ignore[union-attr]


def test_set_lavfi_complex_clears_graph_with_none(backend: MpvPlayerBackend) -> None:
    backend._selected_aid = 1  # noqa: SLF001
    backend._mpv.lavfi_complex = "[aid1]volume=0.5[ao]"  # type: ignore[union-attr]
    backend.set_lavfi_complex(None)
    assert backend._mpv.lavfi_complex == ""  # type: ignore[union-attr]


def test_set_lavfi_complex_rewrites_all_aid_placeholders(backend: MpvPlayerBackend) -> None:
    backend._selected_aid = 1  # noqa: SLF001
    graph = "[aid]asplit=2[a][b];[a]anull[x];[b]anull[ao]"
    backend.set_lavfi_complex(graph)
    assert "[aid1]" in backend._mpv.lavfi_complex  # type: ignore[union-attr]
    assert "[aid]" not in backend._mpv.lavfi_complex  # type: ignore[union-attr]


def test_set_audio_stream_records_selected_aid_by_ordinal(backend: MpvPlayerBackend) -> None:
    assert backend.set_audio_stream(99, audio_ordinal=1) is True
    assert backend._selected_aid == 2  # noqa: SLF001
    assert backend._mpv.aid == 2  # type: ignore[union-attr]


def test_set_audio_stream_records_selected_aid_by_demux_id(backend: MpvPlayerBackend) -> None:
    tracks = [
        {"id": 5, "type": "audio", "demux_id": 1},
        {"id": 6, "type": "audio", "demux_id": 2},
    ]
    backend._mpv.track_list = tracks  # type: ignore[union-attr]
    backend.ingest_track_list(tracks)
    assert backend.set_audio_stream(1) is True
    assert backend._selected_aid == 5  # noqa: SLF001
    assert backend._mpv.aid == 5  # type: ignore[union-attr]


def test_set_audio_stream_records_selected_aid_by_ff_index(backend: MpvPlayerBackend) -> None:
    tracks = [
        {"id": 1, "type": "audio", "demux_id": None, "ff-index": 1},
        {"id": 2, "type": "audio", "demux_id": None, "ff-index": 2},
    ]
    backend._mpv.track_list = tracks  # type: ignore[union-attr]
    backend.ingest_track_list(tracks)
    assert backend.set_audio_stream(2, audio_ordinal=0) is True
    assert backend._selected_aid == 2  # noqa: SLF001


def test_set_volume_maps_linear_slider_to_mpv_cubic(backend: MpvPlayerBackend) -> None:
    assert backend.set_volume(50) is True
    assert backend._mpv.volume == pytest.approx(79.37, rel=0.01)  # type: ignore[union-attr]
    assert backend.volume() == 50


def test_set_volume_linear_quarter(backend: MpvPlayerBackend) -> None:
    assert backend.set_volume(25) is True
    assert backend._mpv.volume == pytest.approx(63.0, rel=0.01)  # type: ignore[union-attr]
    assert backend.volume() == 25


def test_set_volume_clamps_linear_input(backend: MpvPlayerBackend) -> None:
    assert backend.set_volume(131) is True
    assert backend._mpv.volume == 100  # type: ignore[union-attr]
    assert backend.volume() == 100


def test_set_playback_rate_sets_speed_and_direction(backend: MpvPlayerBackend) -> None:
    assert backend.set_playback_rate(2) == 2
    assert backend._mpv.speed == 2  # type: ignore[union-attr]
    assert backend._mpv.play_direction == "forward"  # type: ignore[union-attr]
    assert backend.set_playback_rate(-4) == -4
    assert backend._mpv.speed == 4  # type: ignore[union-attr]
    assert backend._mpv.play_direction == "backward"  # type: ignore[union-attr]
    assert backend.playback_rate() == -4


def test_set_playback_rate_keeps_forward_when_reverse_unavailable(
    backend: MpvPlayerBackend,
) -> None:
    class _NoReverse:
        def __setattr__(self, name: str, value: object) -> None:
            if name == "play_direction" and value == "backward":
                raise RuntimeError("no reverse")
            object.__setattr__(self, name, value)

    backend._mpv = _NoReverse()  # type: ignore[assignment]
    backend._mpv.speed = 1  # type: ignore[union-attr]
    backend._mpv.play_direction = "forward"  # type: ignore[union-attr]
    assert backend.set_playback_rate(2) == 2
    assert backend.set_playback_rate(-2) == 2
    assert backend._mpv.speed == 2  # type: ignore[union-attr]
    assert backend._mpv.play_direction == "forward"  # type: ignore[union-attr]


def test_set_muted_toggles_mpv_mute(backend: MpvPlayerBackend) -> None:
    assert backend.is_muted() is False
    assert backend.set_muted(True) is True
    assert backend._mpv.mute is True  # type: ignore[union-attr]
    assert backend.is_muted() is True
    assert backend.set_muted(False) is True
    assert backend.is_muted() is False
