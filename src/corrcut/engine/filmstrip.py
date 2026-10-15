"""Cached video-lane filmstrip tiles (ffmpeg stills, cover-cropped)."""

from __future__ import annotations

import logging
import os
import subprocess
import threading
import time
from dataclasses import dataclass
from pathlib import Path

from corrcut.engine.hidden_process import no_window_kwargs
from corrcut.engine.jobs.cancel import JobCancelled, check_cancelled
from corrcut.engine.toolchain import resolve_executable

logger = logging.getLogger(__name__)

TIME_QUANTUM_S = 0.05
DEFAULT_ASPECT = 16.0 / 9.0
MIN_TILE_WIDTH = 16
MIN_TILE_HEIGHT = 8
# Tallest video lane is 105px; extract once at an even size and scale in the view.
FILMSTRIP_EXTRACT_HEIGHT = 106
# Bump when still encoding changes (invalidates on-disk JPEGs per source).
FILMSTRIP_STILL_VERSION = 2
EXTRACT_TIMEOUT_S = 20.0
JPEG_QUALITY = 5
# How many viewports either side of the current window to fetch after the visible tiles.
FILMSTRIP_PREFETCH_VIEWPORTS = 2.0
_JPEG_SOI = b"\xff\xd8"
_JPEG_EOI = b"\xff\xd9"


@dataclass(frozen=True)
class FilmstripTile:
    scene_x: float
    width: float
    time_s: float


@dataclass(frozen=True)
class FilmstripQueuePlan:
    """Next extract queue. The first ``primary_count`` times are on screen."""

    cancel: bool
    pending: tuple[float, ...]
    primary_count: int


@dataclass(frozen=True)
class FilmstripExtractDone:
    """Result from a background thumbnail batch; generation invalidates stale results."""

    generation: int
    frames: tuple[tuple[float, Path], ...]
    failed: tuple[float, ...]


def _ratio_value(value: object) -> float | None:
    """Parse ``N:D`` or a positive number. ``0:1`` and ``N/A`` mean unknown."""
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        number = float(value)
        return number if number > 0 else None
    if not isinstance(value, str) or ":" not in value:
        return None
    left, right = value.split(":", 1)
    try:
        num = float(left)
        den = float(right)
    except ValueError:
        return None
    if num <= 0 or den <= 0:
        return None
    return num / den


def _quarter_turn(value: object) -> bool:
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        return False
    try:
        degrees = int(round(float(value)))
    except (TypeError, ValueError):
        return False
    return degrees % 180 == 90


def aspect_from_probe(probe: dict[str, object] | None) -> float:
    """Picture aspect the viewer sees, including non-square pixels and rotation.

    Storage ``width/height`` alone squishes anamorphic frames (720x480 shown as
    3:2 instead of 16:9). Prefer the display aspect, then sample aspect.
    """
    if not isinstance(probe, dict):
        return DEFAULT_ASPECT
    streams = probe.get("video_streams")
    if not isinstance(streams, list) or not streams:
        return DEFAULT_ASPECT
    first = streams[0]
    if not isinstance(first, dict):
        return DEFAULT_ASPECT
    aspect = _ratio_value(first.get("display_aspect_ratio"))
    if aspect is None:
        width = first.get("width")
        height = first.get("height")
        if isinstance(width, (int, float)) and isinstance(height, (int, float)) and height > 0:
            sar = _ratio_value(first.get("sample_aspect_ratio")) or 1.0
            aspect = float(width) / float(height) * sar
    if aspect is None or aspect <= 0:
        return DEFAULT_ASPECT
    if _quarter_turn(first.get("rotation")):
        aspect = 1.0 / aspect
    return aspect


def probe_has_video(probe: dict[str, object] | None) -> bool:
    if not isinstance(probe, dict):
        return False
    streams = probe.get("video_streams")
    return isinstance(streams, list) and bool(streams)


def even_size(value: int, *, minimum: int) -> int:
    size = max(minimum, int(value))
    if size % 2:
        size += 1
    return size


def nearest_even(value: float, *, minimum: int) -> int:
    """Closest even integer at or above an even ``minimum``."""
    floor = minimum if minimum % 2 == 0 else minimum + 1
    snapped = int(round(float(value) / 2.0)) * 2
    return max(floor, snapped)


def cover_crop_source(
    source_width: float,
    source_height: float,
    dest_width: float,
    dest_height: float,
) -> tuple[float, float, float, float]:
    """Source rect that fills ``dest`` without stretching. Returns (x, y, width, height)."""
    if source_width <= 0 or source_height <= 0 or dest_width <= 0 or dest_height <= 0:
        return 0.0, 0.0, max(0.0, source_width), max(0.0, source_height)
    scale = max(dest_width / source_width, dest_height / source_height)
    src_w = dest_width / scale
    src_h = dest_height / scale
    return (source_width - src_w) / 2.0, (source_height - src_h) / 2.0, src_w, src_h


def display_tile_width(lane_height: float, source_width: float, source_height: float) -> float:
    """Tile width for the current lane, preserving the picture aspect exactly."""
    if source_height <= 0 or source_width <= 0 or lane_height <= 0:
        return max(float(MIN_TILE_WIDTH), lane_height)
    return max(float(MIN_TILE_WIDTH), lane_height * source_width / source_height)


def tile_pixel_size(lane_height: float, aspect: float) -> tuple[int, int]:
    """Even extract size closest to ``aspect``. Nearest even width avoids a squish bias."""
    height = even_size(int(round(lane_height)), minimum=MIN_TILE_HEIGHT)
    width = nearest_even(height * max(0.1, aspect), minimum=MIN_TILE_WIDTH)
    return width, height


def filmstrip_extract_pixel_size(aspect: float) -> tuple[int, int]:
    """Disk extract size. Fixed height so lane resize does not create new JPEG sets."""
    return tile_pixel_size(FILMSTRIP_EXTRACT_HEIGHT, aspect)


def filmstrip_time_step(tile_duration_s: float) -> float:
    """Largest ``TIME_QUANTUM_S * 2^n`` that still fits in one tile.

    Small zoom changes stay on the same step so cached stills stay valid.
    """
    quantum = TIME_QUANTUM_S
    if tile_duration_s <= quantum:
        return quantum
    ratio = tile_duration_s / quantum
    power = 1.0
    while power * 2.0 <= ratio:
        power *= 2.0
    return quantum * power


def quantize_time_s(time_s: float, duration: float, *, step: float | None = None) -> float:
    duration = max(0.0, duration)
    quantum = TIME_QUANTUM_S if step is None else max(TIME_QUANTUM_S, float(step))
    quantized = round(max(0.0, time_s) / quantum) * quantum
    if duration <= 0:
        return 0.0
    return min(duration, quantized)


def clamp_seek_time(time_s: float, duration: float) -> float:
    if duration <= 0:
        return 0.0
    return min(max(0.0, time_s), max(0.0, duration - 0.04))


def filmstrip_cache_dir(cache_root: Path, fingerprint: dict[str, object]) -> Path:
    key = str(fingerprint.get("head_sha256") or "x")
    return cache_root / "filmstrip" / f"{key}.v{FILMSTRIP_STILL_VERSION}"


def thumb_path(cache_dir: Path, time_s: float, width: int, height: int) -> Path:
    ms = int(round(max(0.0, time_s) * 1000.0))
    return cache_dir / f"t{ms:08d}_{width}x{height}.jpg"


def filmstrip_still_filter(width: int, height: int) -> str:
    """Cover-crop to square-pixel ``width`` x ``height``, honoring anamorphic SAR.

    ``setsar=1`` alone only tags the output; JPEG has no SAR, so non-square
    sources must be resampled with ``iw*sar`` before the cover-crop scale.
    """
    return (
        "scale=w='if(gt(sar\\,0)\\,round(iw*sar/2)*2\\,iw)':h=ih,"
        "setsar=1,"
        f"scale={width}:{height}:force_original_aspect_ratio=increase,"
        f"crop={width}:{height}"
    )


def filmstrip_tiles(
    *,
    scroll_x: float,
    viewport_width: float,
    pps: float,
    duration: float,
    tile_width: float,
) -> list[FilmstripTile]:
    """Visible cover-crop tiles for the current virtual scroll window."""
    if tile_width <= 0 or pps <= 0 or duration <= 0 or viewport_width <= 0:
        return []
    content_width = duration * pps
    start_x = max(0.0, scroll_x)
    end_x = min(content_width, scroll_x + viewport_width)
    if end_x <= start_x:
        return []
    first_i = int(start_x // tile_width)
    last_i = int((end_x - 0.001) // tile_width)
    step = filmstrip_time_step(tile_width / pps)
    tiles: list[FilmstripTile] = []
    for index in range(first_i, last_i + 1):
        content_left = index * tile_width
        center_s = (content_left + tile_width / 2.0) / pps
        time_s = quantize_time_s(center_s, duration, step=step)
        tiles.append(
            FilmstripTile(
                scene_x=content_left - scroll_x,
                width=tile_width,
                time_s=time_s,
            )
        )
    return tiles


def order_times_from_focus(times: list[float], focus_s: float) -> list[float]:
    """Unique times, nearest ``focus_s`` first. Equal distances keep the earlier time."""
    unique = list(dict.fromkeys(times))
    unique.sort(key=lambda time_s: (abs(time_s - focus_s), time_s))
    return unique


def viewport_focus_s(scroll_x: float, viewport_width: float, pps: float, duration: float) -> float:
    """Timeline time at the middle of the visible window."""
    if pps <= 0:
        return 0.0
    content = max(0.0, duration) * pps
    start = max(0.0, scroll_x)
    end = min(content, max(start, scroll_x + viewport_width))
    return ((start + end) / 2.0) / pps


def filmstrip_priority_times(
    *,
    scroll_x: float,
    viewport_width: float,
    pps: float,
    duration: float,
    tile_width: float,
    prefetch_viewports: float = FILMSTRIP_PREFETCH_VIEWPORTS,
) -> tuple[list[float], list[float]]:
    """Visible tile times, then neighbors, each ordered from the viewport center outward."""
    focus = viewport_focus_s(scroll_x, viewport_width, pps, duration)
    visible = order_times_from_focus(
        [
            tile.time_s
            for tile in filmstrip_tiles(
                scroll_x=scroll_x,
                viewport_width=viewport_width,
                pps=pps,
                duration=duration,
                tile_width=tile_width,
            )
        ],
        focus,
    )
    if prefetch_viewports <= 0 or viewport_width <= 0:
        return visible, []
    margin = viewport_width * prefetch_viewports
    visible_set = set(visible)
    prefetch = order_times_from_focus(
        [
            tile.time_s
            for tile in filmstrip_tiles(
                scroll_x=scroll_x - margin,
                viewport_width=viewport_width + 2.0 * margin,
                pps=pps,
                duration=duration,
                tile_width=tile_width,
            )
            if tile.time_s not in visible_set
        ],
        focus,
    )
    return visible, prefetch


def plan_filmstrip_queue(
    visible: list[float],
    prefetch: list[float],
    in_flight: list[float],
) -> FilmstripQueuePlan:
    """Keep a running batch that still covers the screen; otherwise restart at the new center."""
    visible_unique = list(dict.fromkeys(visible))
    visible_set = set(visible_unique)
    prefetch_unique = [time_s for time_s in dict.fromkeys(prefetch) if time_s not in visible_set]
    queue = tuple(visible_unique + prefetch_unique)
    if not in_flight:
        return FilmstripQueuePlan(cancel=False, pending=queue, primary_count=len(visible_unique))

    inflight_set = set(in_flight)
    overlap = sum(1 for time_s in in_flight if time_s in visible_set)
    covers_screen = bool(visible_set) and (
        inflight_set <= visible_set or overlap * 2 >= len(in_flight)
    )
    still_prefetching = not visible_set and inflight_set <= set(prefetch_unique)
    if not (covers_screen or still_prefetching):
        return FilmstripQueuePlan(cancel=True, pending=queue, primary_count=len(visible_unique))
    rest_visible = [time_s for time_s in visible_unique if time_s not in inflight_set]
    rest_prefetch = [time_s for time_s in prefetch_unique if time_s not in inflight_set]
    return FilmstripQueuePlan(
        cancel=False,
        pending=tuple(rest_visible + rest_prefetch),
        primary_count=len(rest_visible),
    )


def take_filmstrip_batch(
    pending: list[float],
    primary_count: int,
    *,
    limit: int,
) -> tuple[list[float], list[float], int]:
    """Next batch. On-screen times are not mixed into a later neighbor batch."""
    if limit <= 0 or not pending:
        return [], list(pending), max(0, primary_count)
    primary_count = max(0, min(primary_count, len(pending)))
    count = min(limit, primary_count) if primary_count else min(limit, len(pending))
    return list(pending[:count]), list(pending[count:]), max(0, primary_count - count)


def ensure_filmstrip_frames(
    media_path: Path,
    cache_dir: Path,
    times: list[float],
    *,
    width: int,
    height: int,
    duration: float,
    ffmpeg: str | None = None,
    cancel_event: threading.Event | None = None,
) -> FilmstripExtractDone:
    """Extract missing JPEGs for ``times`` in one ffmpeg seek pass.

    Cached stills are reused. Generation is filled by the caller.
    """
    cache_dir.mkdir(parents=True, exist_ok=True)
    ready: dict[float, Path] = {}
    missing: list[tuple[float, Path]] = []
    seen_missing: set[float] = set()
    for time_s in times:
        check_cancelled(cancel_event)
        dest = thumb_path(cache_dir, time_s, width, height)
        if _cached_jpeg(dest):
            ready[time_s] = dest
            continue
        if time_s in seen_missing:
            continue
        seen_missing.add(time_s)
        missing.append((time_s, dest))
    if missing:
        extracted, _failed = _extract_stills(
            media_path,
            missing,
            width=width,
            height=height,
            duration=duration,
            ffmpeg=ffmpeg,
            cancel_event=cancel_event,
        )
        for time_s, path in extracted:
            ready[time_s] = path
    frames: list[tuple[float, Path]] = []
    failed: list[float] = []
    reported_failed: set[float] = set()
    for time_s in times:
        cached_path = ready.get(time_s)
        if cached_path is not None:
            frames.append((time_s, cached_path))
        elif time_s not in reported_failed:
            reported_failed.add(time_s)
            failed.append(time_s)
    return FilmstripExtractDone(generation=0, frames=tuple(frames), failed=tuple(failed))


def extract_frame(
    media_path: Path,
    dest: Path,
    time_s: float,
    *,
    width: int,
    height: int,
    duration: float,
    ffmpeg: str | None = None,
    cancel_event: threading.Event | None = None,
) -> None:
    """Seek to ``time_s`` and write a cover-cropped JPEG of ``width`` x ``height``."""
    frames, failed = _extract_stills(
        media_path,
        [(time_s, dest)],
        width=width,
        height=height,
        duration=duration,
        ffmpeg=ffmpeg,
        cancel_event=cancel_event,
    )
    if failed or not frames:
        raise RuntimeError("ffmpeg filmstrip extract failed")


def _extract_stills(
    media_path: Path,
    items: list[tuple[float, Path]],
    *,
    width: int,
    height: int,
    duration: float,
    ffmpeg: str | None = None,
    cancel_event: threading.Event | None = None,
) -> tuple[list[tuple[float, Path]], list[float]]:
    """Seek each missing time in a single ffmpeg process and publish complete JPEGs."""
    if not items:
        return [], []
    check_cancelled(cancel_event)
    exe = resolve_executable("ffmpeg") if ffmpeg is None else ffmpeg
    ordered = sorted(items, key=lambda item: item[0])
    for _time_s, dest in ordered:
        dest.parent.mkdir(parents=True, exist_ok=True)
    cmd, temps = _batch_still_command(
        exe,
        media_path,
        ordered,
        width=width,
        height=height,
        duration=duration,
    )
    for tmp in temps:
        _unlink_quiet(tmp)
    stderr = ""
    try:
        stderr = _run_ffmpeg_batch(
            cmd,
            cancel_event=cancel_event,
            timeout_s=EXTRACT_TIMEOUT_S * len(ordered),
        )
    except JobCancelled:
        _promote_stills(ordered, temps)
        raise
    except Exception as exc:
        stderr = str(exc)
    frames, failed = _promote_stills(ordered, temps)
    if failed:
        detail = stderr.strip().splitlines()[-1] if stderr.strip() else "incomplete"
        log = logger.warning if len(failed) == len(ordered) else logger.debug
        log("Filmstrip extract missed %d/%d frame(s): %s", len(failed), len(ordered), detail)
    return frames, failed


def _batch_still_command(
    exe: str,
    media_path: Path,
    items: list[tuple[float, Path]],
    *,
    width: int,
    height: int,
    duration: float,
) -> tuple[list[str], list[Path]]:
    vf = filmstrip_still_filter(width, height)
    cmd: list[str] = [exe, "-hide_banner", "-loglevel", "error", "-nostdin", "-y"]
    for time_s, _dest in items:
        seek = clamp_seek_time(time_s, duration)
        cmd.extend(["-ss", f"{seek:.3f}", "-i", str(media_path)])
    temps: list[Path] = []
    for index, (_time_s, dest) in enumerate(items):
        tmp = dest.with_name(f"{dest.stem}.{index}.part.jpg")
        temps.append(tmp)
        cmd.extend(
            [
                "-map",
                f"{index}:v:0",
                "-an",
                "-frames:v",
                "1",
                "-vf",
                vf,
                "-q:v",
                str(JPEG_QUALITY),
                str(tmp),
            ]
        )
    return cmd, temps


def _promote_stills(
    items: list[tuple[float, Path]],
    temps: list[Path],
) -> tuple[list[tuple[float, Path]], list[float]]:
    frames: list[tuple[float, Path]] = []
    failed: list[float] = []
    for (time_s, dest), tmp in zip(items, temps, strict=True):
        if not _jpeg_is_complete(tmp):
            _unlink_quiet(tmp)
            failed.append(time_s)
            continue
        try:
            tmp.replace(dest)
        except OSError:
            _unlink_quiet(tmp)
            failed.append(time_s)
            continue
        frames.append((time_s, dest))
    return frames, failed


def _cached_jpeg(path: Path) -> bool:
    try:
        return path.is_file() and path.stat().st_size > 0
    except OSError:
        return False


def _jpeg_is_complete(path: Path) -> bool:
    try:
        if not path.is_file() or path.stat().st_size < 4:
            return False
        with path.open("rb") as handle:
            if handle.read(2) != _JPEG_SOI:
                return False
            handle.seek(-2, os.SEEK_END)
            return handle.read(2) == _JPEG_EOI
    except OSError:
        return False


def _unlink_quiet(path: Path) -> None:
    try:
        path.unlink(missing_ok=True)
    except OSError:
        return


def _run_ffmpeg_batch(
    cmd: list[str],
    *,
    cancel_event: threading.Event | None = None,
    timeout_s: float,
) -> str:
    """Run one ffmpeg process. Raises ``JobCancelled`` or ``RuntimeError`` on timeout.

    A non-zero exit still returns stderr so the caller can keep any finished JPEGs.
    """
    check_cancelled(cancel_event)
    proc = subprocess.Popen(
        cmd,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        **no_window_kwargs(),
    )
    deadline = time.monotonic() + timeout_s
    comm_result: list[tuple[bytes, bytes] | BaseException] = []

    def _communicate() -> None:
        try:
            comm_result.append(proc.communicate())
        except Exception as exc:
            comm_result.append(exc)

    reader = threading.Thread(target=_communicate, daemon=True)
    reader.start()
    try:
        while reader.is_alive():
            check_cancelled(cancel_event)
            if time.monotonic() > deadline:
                _stop_ffmpeg(proc, reader)
                raise RuntimeError("ffmpeg filmstrip extract timed out")
            time.sleep(0.05)
        reader.join(timeout=5)
    except JobCancelled:
        _stop_ffmpeg(proc, reader)
        raise

    if not comm_result:
        raise RuntimeError("ffmpeg filmstrip extract produced no output")
    outcome = comm_result[0]
    if isinstance(outcome, BaseException):
        raise outcome
    _stdout, stderr = outcome
    return stderr.decode(errors="replace")


def _stop_ffmpeg(proc: subprocess.Popen[bytes], reader: threading.Thread) -> None:
    proc.kill()
    reader.join(timeout=5)
    proc.wait(timeout=5)
