"""Detect hard scene cuts via FFmpeg (snap targets for timeline edits)."""

from __future__ import annotations

import logging
import os
import re
import subprocess
import sys
import tempfile
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from corrcut.engine.jobs.cancel import JobCancelled, check_cancelled
from corrcut.engine.toolchain import resolve_executable

logger = logging.getLogger(__name__)

DEFAULT_THRESHOLD = 0.4
MIN_CUT_GAP_S = 0.12
SCALE_WIDTH = 160
# Cap decoder/filter threads so we do not flood the GPU alongside preview.
DETECT_THREADS = 2
# Full-title decode; cancel is the real bound.
DETECT_TIMEOUT_S = 6 * 60 * 60


def _low_priority_popen_kwargs() -> dict[str, object]:
    """Run scene-cut ffmpeg below the UI/preview so GPU/CPU prefer playback.

    Hardware decode still runs on the GPU; Windows WDDM and the scheduler
    just favor higher-priority processes (mpv / the editor) when contended.
    """
    if sys.platform != "win32":
        return {}
    from corrcut.engine.hidden_process import no_window_kwargs

    return no_window_kwargs(extra_creationflags=subprocess.IDLE_PRIORITY_CLASS)


def _lower_child_priority(proc: subprocess.Popen[str]) -> None:
    if sys.platform == "win32" or proc.pid is None:
        return
    try:
        # Nice range is typically -20..19; positive = lower priority.
        os.setpriority(os.PRIO_PROCESS, proc.pid, 15)
    except (AttributeError, OSError, PermissionError):
        return

_PTS_TIME_RE = re.compile(r"pts_time:([0-9]+(?:\.[0-9]+)?)")
_OUT_TIME_US_RE = re.compile(r"out_time_us=(\d+)")
_OUT_TIME_MS_RE = re.compile(r"out_time_ms=(\d+)")
_DURATION_RE = re.compile(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)")


@dataclass(frozen=True)
class SceneCutDetectDone:
    """Result from a background scene-cut detection job."""

    generation: int
    fingerprint: dict[str, object]
    threshold: float
    times: tuple[float, ...]


def parse_scene_cut_times(
    text: str,
    *,
    min_gap_s: float = MIN_CUT_GAP_S,
) -> list[float]:
    """Extract sorted unique cut times from FFmpeg metadata=print output."""
    raw: list[float] = []
    for match in _PTS_TIME_RE.finditer(text):
        try:
            raw.append(float(match.group(1)))
        except ValueError:
            continue
    if not raw:
        return []
    raw.sort()
    out: list[float] = [raw[0]]
    gap = max(0.0, float(min_gap_s))
    for t in raw[1:]:
        if t - out[-1] >= gap:
            out.append(t)
    return out


def _parse_duration_s(stderr_text: str) -> float | None:
    match = _DURATION_RE.search(stderr_text)
    if not match:
        return None
    hours = int(match.group(1))
    minutes = int(match.group(2))
    seconds = float(match.group(3))
    return hours * 3600.0 + minutes * 60.0 + seconds


def _read_progress_seconds(progress_path: Path) -> float | None:
    if not progress_path.is_file():
        return None
    try:
        text = progress_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    us_matches = list(_OUT_TIME_US_RE.finditer(text))
    if us_matches:
        return int(us_matches[-1].group(1)) / 1_000_000.0
    ms_matches = list(_OUT_TIME_MS_RE.finditer(text))
    if ms_matches:
        return int(ms_matches[-1].group(1)) / 1_000_000.0
    return None


def detect_scene_cuts(
    media_path: Path,
    *,
    threshold: float = DEFAULT_THRESHOLD,
    ffmpeg: str | None = None,
    cancel_event: threading.Event | None = None,
    timeout_s: float = DETECT_TIMEOUT_S,
    progress: Callable[[int, str], None] | None = None,
    duration_hint_s: float | None = None,
    software: bool = False,
) -> list[float]:
    """Run a downscaled FFmpeg scene filter and return cut timestamps in seconds.

    Still a full decode of the video stream (required for accurate hard cuts).
    Hardware decode is used when available, at idle/low process priority so
    preview keeps the GPU. Progress is reported so long titles do not look hung.
    """
    check_cancelled(cancel_event)
    exe = resolve_executable("ffmpeg") if ffmpeg is None else ffmpeg
    thresh = max(0.01, min(1.0, float(threshold)))
    # No shell quotes: argv passes the filter string literally.
    vf = f"scale={SCALE_WIDTH}:-2,select=gt(scene\\,{thresh}),metadata=print"

    with tempfile.TemporaryDirectory(prefix="ms_scenedetect_") as tmp:
        progress_path = Path(tmp) / "progress.txt"
        cmd = [exe, "-hide_banner", "-threads", str(DETECT_THREADS)]
        if not software:
            cmd.extend(["-hwaccel", "auto"])
        cmd.extend(
            [
                "-i",
                str(media_path),
                "-an",
                "-map",
                "0:v:0",
                "-vf",
                vf,
                "-f",
                "null",
                "-progress",
                str(progress_path),
                "-y",
                "-",
            ]
        )
        logger.info(
            "Detecting scene cuts%s: %s",
            " (software)" if software else "",
            media_path.name,
        )
        check_cancelled(cancel_event)
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
            **_low_priority_popen_kwargs(),
        )
        _lower_child_priority(proc)
        stderr_chunks: list[str] = []
        reader_done = threading.Event()

        def _read_stderr() -> None:
            assert proc.stderr is not None
            try:
                for line in proc.stderr:
                    stderr_chunks.append(line)
            finally:
                reader_done.set()

        reader = threading.Thread(target=_read_stderr, daemon=True)
        reader.start()
        deadline = time.monotonic() + timeout_s
        duration_s = float(duration_hint_s) if duration_hint_s and duration_hint_s > 0 else None
        last_pct = -1
        try:
            while proc.poll() is None or not reader_done.is_set():
                check_cancelled(cancel_event)
                if time.monotonic() > deadline:
                    proc.kill()
                    reader.join(timeout=5)
                    proc.wait(timeout=5)
                    raise RuntimeError("ffmpeg scene-cut detect timed out")
                if duration_s is None and stderr_chunks:
                    duration_s = _parse_duration_s("".join(stderr_chunks[:40]))
                out_s = _read_progress_seconds(progress_path)
                if progress is not None and out_s is not None and duration_s and duration_s > 0:
                    pct = min(99, int(out_s * 100 / duration_s))
                    if pct != last_pct:
                        last_pct = pct
                        progress(pct, f"Detecting scene cuts… {pct}%")
                time.sleep(0.2)
            reader.join(timeout=5)
        except JobCancelled:
            proc.kill()
            reader.join(timeout=5)
            proc.wait(timeout=5)
            raise

        text = "".join(stderr_chunks)
        if proc.returncode not in (0, None) and not _PTS_TIME_RE.search(text):
            if not software:
                logger.warning(
                    "Scene-cut detect with hwaccel failed (exit %s); retrying software",
                    proc.returncode,
                )
                return detect_scene_cuts(
                    media_path,
                    threshold=threshold,
                    ffmpeg=exe,
                    cancel_event=cancel_event,
                    timeout_s=timeout_s,
                    progress=progress,
                    duration_hint_s=duration_hint_s,
                    software=True,
                )
            detail = text.strip().splitlines()[-8:]
            raise RuntimeError(
                "\n".join(detail) if detail else "ffmpeg scene-cut detect failed"
            )
        if progress is not None:
            progress(100, "Detecting scene cuts… 100%")
        return parse_scene_cut_times(text)
