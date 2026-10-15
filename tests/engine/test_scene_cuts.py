"""Scene-cut metadata parsing and optional FFmpeg detection."""

from pathlib import Path

import pytest

from corrcut.engine.scene_cuts import (
    detect_scene_cuts,
    parse_scene_cut_times,
    _parse_duration_s,
)
from corrcut.engine.toolchain import bundled_executable


def test_parse_scene_cut_times_extracts_pts() -> None:
    text = """
frame:0 pts:0 pts_time:0
lavfi.scene_score=0.012345
frame:24 pts:1024 pts_time:1.024
lavfi.scene_score=0.512
frame:48 pts:2048 pts_time:2.048
"""
    assert parse_scene_cut_times(text) == pytest.approx([0.0, 1.024, 2.048])


def test_parse_scene_cut_times_dedupes_close_flashes() -> None:
    text = "pts_time:10.0\npts_time:10.05\npts_time:10.20\n"
    assert parse_scene_cut_times(text, min_gap_s=0.12) == pytest.approx([10.0, 10.20])


def test_parse_scene_cut_times_empty() -> None:
    assert parse_scene_cut_times("") == []
    assert parse_scene_cut_times("no timestamps here") == []


def test_parse_duration_from_ffmpeg_banner() -> None:
    text = "Input #0, matroska:\n  Duration: 01:55:12.48, start: 0.000000, bitrate: 8000 kb/s\n"
    assert _parse_duration_s(text) == pytest.approx(1 * 3600 + 55 * 60 + 12.48)


def test_low_priority_popen_kwargs_on_windows() -> None:
    import subprocess
    import sys

    from corrcut.engine.scene_cuts import _low_priority_popen_kwargs

    kwargs = _low_priority_popen_kwargs()
    if sys.platform == "win32":
        flags = kwargs.get("creationflags")
        assert isinstance(flags, int)
        assert flags & subprocess.IDLE_PRIORITY_CLASS
        assert flags & subprocess.CREATE_NO_WINDOW
    else:
        assert kwargs == {}


def test_detect_scene_cuts_on_color_change(tmp_path: Path) -> None:
    ffmpeg = bundled_executable("ffmpeg")
    if not ffmpeg:
        pytest.skip("bundled ffmpeg not present")
    import subprocess

    media = tmp_path / "cuts.mp4"
    # Two solid colors hard-cut at 0.5s.
    created = subprocess.run(
        [
            ffmpeg,
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            "color=c=red:s=320x180:r=10:d=0.5",
            "-f",
            "lavfi",
            "-i",
            "color=c=blue:s=320x180:r=10:d=0.5",
            "-filter_complex",
            "[0:v][1:v]concat=n=2:v=1:a=0[v]",
            "-map",
            "[v]",
            "-pix_fmt",
            "yuv420p",
            str(media),
        ],
        check=False,
        capture_output=True,
        timeout=30,
    )
    if created.returncode != 0 or not media.is_file():
        pytest.skip("could not generate color-cut clip")

    times = detect_scene_cuts(media, threshold=0.3, ffmpeg=ffmpeg, timeout_s=60.0)
    assert times, "expected at least one hard cut"
    # Cut should land near the concat join (~0.5s).
    nearest = min(times, key=lambda t: abs(t - 0.5))
    assert abs(nearest - 0.5) < 0.25


def _captured_scene_command(monkeypatch: pytest.MonkeyPatch, *, software: bool) -> list[str]:
    import io

    captured: dict[str, list[str]] = {}

    class _Proc:
        returncode = 0
        stderr = io.StringIO("")

        def poll(self) -> int:
            return 0

        def kill(self) -> None:
            return None

        def wait(self, timeout: float | None = None) -> int:
            del timeout
            return 0

    def _popen(cmd: list[str], **_kwargs: object) -> _Proc:
        captured["cmd"] = list(cmd)
        return _Proc()

    monkeypatch.setattr("corrcut.engine.scene_cuts.subprocess.Popen", _popen)
    monkeypatch.setattr("corrcut.engine.scene_cuts.time.sleep", lambda _seconds: None)
    detect_scene_cuts(Path("clip.mkv"), ffmpeg="ffmpeg", software=software, timeout_s=5)
    return captured["cmd"]


def test_hardware_scene_detect_requests_hwaccel(monkeypatch: pytest.MonkeyPatch) -> None:
    cmd = _captured_scene_command(monkeypatch, software=False)
    assert "-hwaccel" in cmd
    assert cmd[cmd.index("-hwaccel") + 1] == "auto"


def test_software_scene_detect_skips_hwaccel(monkeypatch: pytest.MonkeyPatch) -> None:
    cmd = _captured_scene_command(monkeypatch, software=True)
    assert "-hwaccel" not in cmd
