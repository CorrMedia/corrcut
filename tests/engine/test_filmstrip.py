"""Filmstrip tile layout and cache paths (no ffmpeg)."""

import threading
from pathlib import Path

import pytest

from corrcut.engine.filmstrip import (
    DEFAULT_ASPECT,
    FILMSTRIP_EXTRACT_HEIGHT,
    FILMSTRIP_STILL_VERSION,
    aspect_from_probe,
    cover_crop_source,
    display_tile_width,
    ensure_filmstrip_frames,
    extract_frame,
    filmstrip_cache_dir,
    filmstrip_extract_pixel_size,
    filmstrip_priority_times,
    filmstrip_still_filter,
    filmstrip_tiles,
    filmstrip_time_step,
    order_times_from_focus,
    plan_filmstrip_queue,
    probe_has_video,
    quantize_time_s,
    take_filmstrip_batch,
    thumb_path,
    tile_pixel_size,
    viewport_focus_s,
)
from corrcut.engine.jobs.cancel import JobCancelled
from corrcut.engine.toolchain import bundled_executable

_MIN_JPEG = b"\xff\xd8\xff\xd9"


def test_quantize_time_snaps_to_50ms() -> None:
    assert quantize_time_s(1.024, 10.0) == pytest.approx(1.0)
    assert quantize_time_s(1.026, 10.0) == pytest.approx(1.05)
    assert quantize_time_s(99.0, 2.0) == 2.0
    assert quantize_time_s(-1.0, 2.0) == 0.0


def test_display_tile_width_tracks_lane_height() -> None:
    assert display_tile_width(72.0, 128.0, 72.0) == 128.0
    assert display_tile_width(36.0, 128.0, 72.0) == 64.0


def test_cover_crop_source_keeps_aspect_when_dest_is_shorter() -> None:
    x, y, width, height = cover_crop_source(160, 90, 160, 65)
    assert width == pytest.approx(160)
    assert height == pytest.approx(65)
    assert x == pytest.approx(0)
    assert y == pytest.approx(12.5)
    assert width / height == pytest.approx(160 / 65)


def test_tile_pixel_size_is_even_cover_crop() -> None:
    width, height = tile_pixel_size(72.0, 16 / 9)
    assert height == 72
    assert width == 128
    assert width % 2 == 0
    assert height % 2 == 0


def test_filmstrip_extract_pixel_size_uses_fixed_height() -> None:
    width, height = filmstrip_extract_pixel_size(16 / 9)
    assert height == FILMSTRIP_EXTRACT_HEIGHT
    assert height % 2 == 0
    assert width % 2 == 0
    assert width / height == pytest.approx(16 / 9, rel=0.02)


def test_aspect_from_probe_uses_first_video_stream() -> None:
    assert aspect_from_probe({}) == DEFAULT_ASPECT
    assert not probe_has_video({})
    probe = {"video_streams": [{"width": 1920, "height": 800}]}
    assert probe_has_video(probe)
    assert aspect_from_probe(probe) == 1920 / 800


def test_aspect_from_probe_uses_display_aspect_for_anamorphic_video() -> None:
    """720x480 storage with a 16:9 display ratio must not stay 3:2."""
    probe = {
        "video_streams": [
            {
                "width": 720,
                "height": 480,
                "sample_aspect_ratio": "853:720",
                "display_aspect_ratio": "853:480",
            }
        ]
    }
    assert aspect_from_probe(probe) == pytest.approx(853 / 480)
    width, height = tile_pixel_size(85.0, aspect_from_probe(probe))
    assert width / height == pytest.approx(853 / 480, rel=0.02)
    assert width / height > 1.7


def test_aspect_from_probe_applies_sample_aspect_and_rotation() -> None:
    sar_only = {
        "video_streams": [
            {"width": 720, "height": 480, "sample_aspect_ratio": "32:27"}
        ]
    }
    assert aspect_from_probe(sar_only) == pytest.approx(16 / 9)
    turned = {
        "video_streams": [
            {"width": 1920, "height": 1080, "display_aspect_ratio": "16:9", "rotation": 90}
        ]
    }
    assert aspect_from_probe(turned) == pytest.approx(9 / 16)


def test_filmstrip_time_step_is_power_of_two_quantum() -> None:
    assert filmstrip_time_step(0.04) == pytest.approx(0.05)
    assert filmstrip_time_step(0.05) == pytest.approx(0.05)
    assert filmstrip_time_step(0.10) == pytest.approx(0.10)
    assert filmstrip_time_step(10.0) == pytest.approx(6.4)


def test_filmstrip_tiles_stable_times_while_scrolling() -> None:
    zoomed_out = filmstrip_tiles(
        scroll_x=0.0,
        viewport_width=400.0,
        pps=10.0,
        duration=60.0,
        tile_width=100.0,
    )
    assert zoomed_out
    # 100px tiles at 10 px/s => 10s/tile, ladder step 6.4s, first center 5s -> 6.4s.
    assert zoomed_out[0].time_s == pytest.approx(6.4)
    assert zoomed_out[0].width == 100.0

    scrolled = filmstrip_tiles(
        scroll_x=50.0,
        viewport_width=400.0,
        pps=10.0,
        duration=60.0,
        tile_width=100.0,
    )
    assert scrolled[0].scene_x == -50.0
    assert scrolled[0].time_s == zoomed_out[0].time_s


def test_small_zoom_reuses_time_ladder() -> None:
    tile_width = 128.0
    pps = tile_width / 2.4
    first = filmstrip_tiles(
        scroll_x=0.0,
        viewport_width=640.0,
        pps=pps,
        duration=60.0,
        tile_width=tile_width,
    )
    nudged = filmstrip_tiles(
        scroll_x=0.0,
        viewport_width=640.0,
        pps=pps * 1.15,
        duration=60.0,
        tile_width=tile_width,
    )
    shared = {tile.time_s for tile in first} & {tile.time_s for tile in nudged}
    assert shared


def test_zoom_in_samples_finer_times_in_view() -> None:
    wide = filmstrip_tiles(
        scroll_x=0.0,
        viewport_width=400.0,
        pps=20.0,
        duration=30.0,
        tile_width=100.0,
    )
    tight = filmstrip_tiles(
        scroll_x=0.0,
        viewport_width=400.0,
        pps=200.0,
        duration=30.0,
        tile_width=100.0,
    )
    wide_times = {tile.time_s for tile in wide}
    tight_times = {tile.time_s for tile in tight}
    assert len(tight_times) >= len(wide_times)
    assert max(tight_times) < max(wide_times)


def test_priority_times_load_the_viewport_center_then_neighbors() -> None:
    scroll_x = 2000.0
    viewport_width = 400.0
    pps = 10.0
    duration = 1000.0
    visible, prefetch = filmstrip_priority_times(
        scroll_x=scroll_x,
        viewport_width=viewport_width,
        pps=pps,
        duration=duration,
        tile_width=100.0,
    )
    focus = viewport_focus_s(scroll_x, viewport_width, pps, duration)
    assert visible == order_times_from_focus(visible, focus)
    assert prefetch == order_times_from_focus(prefetch, focus)
    assert visible[0] == min(visible, key=lambda time_s: (abs(time_s - focus), time_s))
    assert set(visible).isdisjoint(prefetch)
    assert max(abs(time_s - focus) for time_s in prefetch) > max(
        abs(time_s - focus) for time_s in visible
    )
    assert max(prefetch) < duration


def test_plan_restarts_when_the_view_moves() -> None:
    plan = plan_filmstrip_queue([10.0, 11.0, 12.0], [13.0, 14.0], [1.0, 2.0, 3.0])
    assert plan.cancel
    assert plan.pending[:3] == (10.0, 11.0, 12.0)
    assert plan.primary_count == 3


def test_plan_keeps_a_batch_that_still_covers_the_screen() -> None:
    plan = plan_filmstrip_queue([10.0, 11.0, 12.0, 13.0], [20.0, 21.0], [10.0, 11.0])
    assert not plan.cancel
    assert plan.pending == (12.0, 13.0, 20.0, 21.0)
    assert plan.primary_count == 2


def test_plan_keeps_neighbor_prefetch_after_the_screen_is_filled() -> None:
    plan = plan_filmstrip_queue([], [20.0, 21.0, 22.0], [20.0, 21.0])
    assert not plan.cancel
    assert plan.pending == (22.0,)
    assert plan.primary_count == 0


def test_take_filmstrip_batch_finishes_the_screen_before_neighbors() -> None:
    batch, rest, primary = take_filmstrip_batch([1.0, 2.0, 3.0, 9.0, 10.0], 3, limit=12)
    assert batch == [1.0, 2.0, 3.0]
    assert rest == [9.0, 10.0]
    assert primary == 0
    next_batch, leftover, next_primary = take_filmstrip_batch(rest, primary, limit=12)
    assert next_batch == [9.0, 10.0]
    assert leftover == []
    assert next_primary == 0


def test_thumb_path_includes_time_and_size(tmp_path: Path) -> None:
    cache = filmstrip_cache_dir(tmp_path, {"head_sha256": "abc123"})
    assert cache == tmp_path / "filmstrip" / f"abc123.v{FILMSTRIP_STILL_VERSION}"
    path = thumb_path(cache, 12.35, 128, 72)
    assert path.parent == cache
    assert path.name == "t00012350_128x72.jpg"


def test_filmstrip_still_filter_bakes_sar_before_cover_crop() -> None:
    vf = filmstrip_still_filter(188, 106)
    assert "iw*sar" in vf
    assert vf.index("iw*sar") < vf.index("force_original_aspect_ratio")
    assert "setsar=1" in vf
    assert "crop=188:106" in vf


def test_extract_frame_writes_cover_crop_jpeg(tmp_path: Path) -> None:
    ffmpeg = bundled_executable("ffmpeg")
    if not ffmpeg:
        pytest.skip("bundled ffmpeg not present")
    import subprocess

    from PySide6.QtGui import QImage

    media = tmp_path / "src.mp4"
    created = subprocess.run(
        [
            ffmpeg,
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            "testsrc=size=320x180:rate=10",
            "-t",
            "1",
            "-pix_fmt",
            "yuv420p",
            str(media),
        ],
        check=False,
        capture_output=True,
        timeout=30,
    )
    if created.returncode != 0 or not media.is_file():
        pytest.skip("could not generate testsrc clip")
    dest = tmp_path / "out.jpg"
    extract_frame(
        media,
        dest,
        0.2,
        width=128,
        height=72,
        duration=1.0,
        ffmpeg=ffmpeg,
    )
    assert dest.is_file()
    image = QImage(str(dest))
    assert not image.isNull()
    assert image.width() == 128
    assert image.height() == 72

    done = ensure_filmstrip_frames(
        media,
        tmp_path / "batch",
        [0.1, 0.4, 0.7],
        width=128,
        height=72,
        duration=1.0,
        ffmpeg=ffmpeg,
    )
    assert done.failed == ()
    assert [time_s for time_s, _path in done.frames] == [0.1, 0.4, 0.7]
    for _time_s, path in done.frames:
        frame = QImage(str(path))
        assert not frame.isNull()
        assert frame.width() == 128
        assert frame.height() == 72


def test_extract_frame_unsquishes_anamorphic_sar(tmp_path: Path) -> None:
    """720x480 with SAR 32:27 must land in a true 16:9 JPEG, not storage 3:2."""
    ffmpeg = bundled_executable("ffmpeg")
    if not ffmpeg:
        pytest.skip("bundled ffmpeg not present")
    import subprocess

    from PySide6.QtGui import QImage

    media = tmp_path / "ana.mp4"
    created = subprocess.run(
        [
            ffmpeg,
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            "testsrc=size=720x480:rate=10",
            "-t",
            "1",
            "-pix_fmt",
            "yuv420p",
            "-aspect",
            "16:9",
            str(media),
        ],
        check=False,
        capture_output=True,
        timeout=30,
    )
    if created.returncode != 0 or not media.is_file():
        pytest.skip("could not generate anamorphic clip")
    width, height = filmstrip_extract_pixel_size(16 / 9)
    dest = tmp_path / "ana.jpg"
    extract_frame(
        media,
        dest,
        0.2,
        width=width,
        height=height,
        duration=1.0,
        ffmpeg=ffmpeg,
    )
    image = QImage(str(dest))
    assert not image.isNull()
    assert image.width() == width
    assert image.height() == height
    assert image.width() / image.height() == pytest.approx(16 / 9, rel=0.02)


class _FakeFfmpeg:
    def __init__(self, cmd: list[str], *, parts: set[str] | None = None) -> None:
        self.returncode: int | None = 0
        self.cmd = list(cmd)
        for arg in cmd:
            if not str(arg).endswith(".part.jpg"):
                continue
            if parts is not None and Path(arg).name not in parts:
                continue
            Path(arg).parent.mkdir(parents=True, exist_ok=True)
            Path(arg).write_bytes(_MIN_JPEG)

    def communicate(self) -> tuple[bytes, bytes]:
        while self.returncode is None:
            threading.Event().wait(0.01)
        return b"", b""

    def kill(self) -> None:
        self.returncode = -9

    def wait(self, timeout: float | None = None) -> int:
        return 0 if self.returncode is None else self.returncode


def test_ensure_filmstrip_frames_seeks_missing_times_in_one_process(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[list[str]] = []

    def _popen(cmd: list[str], **_kwargs: object) -> _FakeFfmpeg:
        calls.append(list(cmd))
        return _FakeFfmpeg(cmd)

    monkeypatch.setattr("corrcut.engine.filmstrip.subprocess.Popen", _popen)
    cache = tmp_path / "cache"
    cached = thumb_path(cache, 1.0, 128, 72)
    cached.parent.mkdir(parents=True)
    cached.write_bytes(b"cached")
    media = tmp_path / "clip.mp4"
    media.write_bytes(b"media")

    done = ensure_filmstrip_frames(
        media,
        cache,
        [1.0, 3.0, 2.0, 3.0],
        width=128,
        height=72,
        duration=10.0,
        ffmpeg="ffmpeg",
    )

    assert len(calls) == 1
    command = calls[0]
    assert command.count("-ss") == 2
    assert "2.000" in command
    assert "3.000" in command
    assert "1.000" not in command
    assert [time_s for time_s, _path in done.frames] == [1.0, 3.0, 2.0, 3.0]
    assert done.failed == ()
    assert list(cache.glob("*.part.jpg")) == []


def test_ensure_filmstrip_frames_keeps_finished_jpegs_when_ffmpeg_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def _popen(cmd: list[str], **_kwargs: object) -> _FakeFfmpeg:
        proc = _FakeFfmpeg(cmd, parts={"t00002000_128x72.0.part.jpg"})
        proc.returncode = 1
        return proc

    monkeypatch.setattr("corrcut.engine.filmstrip.subprocess.Popen", _popen)
    cache = tmp_path / "cache"
    media = tmp_path / "clip.mp4"
    media.write_bytes(b"media")

    done = ensure_filmstrip_frames(
        media,
        cache,
        [5.0, 2.0],
        width=128,
        height=72,
        duration=10.0,
        ffmpeg="ffmpeg",
    )

    assert [time_s for time_s, _path in done.frames] == [2.0]
    assert done.failed == (5.0,)
    assert done.frames[0][1].is_file()


def test_ensure_filmstrip_frames_cancel_stops_the_batch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cancel = threading.Event()

    def _popen(cmd: list[str], **_kwargs: object) -> _FakeFfmpeg:
        cancel.set()
        proc = _FakeFfmpeg(cmd, parts=set())
        proc.returncode = None
        return proc

    monkeypatch.setattr("corrcut.engine.filmstrip.subprocess.Popen", _popen)
    cache = tmp_path / "cache"
    media = tmp_path / "clip.mp4"
    media.write_bytes(b"media")

    with pytest.raises(JobCancelled):
        ensure_filmstrip_frames(
            media,
            cache,
            [1.0, 2.0],
            width=128,
            height=72,
            duration=10.0,
            ffmpeg="ffmpeg",
            cancel_event=cancel,
        )
    assert list(cache.glob("*.part.jpg")) == []
