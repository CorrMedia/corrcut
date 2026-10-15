"""Detect baked-in letterbox / pillarbox mattes from decoded frames.

The bundled FFmpeg build does not include ``cropdetect``, so we extract a few
grayscale frames and scan for near-black borders with NumPy.
"""

from __future__ import annotations

import logging
import subprocess
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from corrcut.engine.hidden_process import no_window_kwargs
from corrcut.engine.toolchain import resolve_executable

logger = logging.getLogger(__name__)

DEFAULT_CROPDETECT_LIMIT = 24.0
DETECT_TIMEOUT_S = 60.0
# Downscale for speed; letterbox ratios are preserved.
ANALYZE_WIDTH = 480


@dataclass(frozen=True)
class PixelCrop:
    width: int
    height: int
    x: int
    y: int

    def as_normalized(self, frame_w: int, frame_h: int) -> dict[str, float]:
        fw = max(1, int(frame_w))
        fh = max(1, int(frame_h))
        return {
            "x": max(0.0, min(1.0, self.x / fw)),
            "y": max(0.0, min(1.0, self.y / fh)),
            "width": max(0.0, min(1.0, self.width / fw)),
            "height": max(0.0, min(1.0, self.height / fh)),
        }


def _sample_seek_times(duration_s: float | None) -> list[float]:
    if duration_s is None or duration_s <= 0:
        return [0.0]
    if duration_s < 10.0:
        return [max(0.0, duration_s * 0.2)]
    return [
        duration_s * 0.15,
        duration_s * 0.45,
        duration_s * 0.75,
    ]


def _even(value: int) -> int:
    return max(2, int(value) - (int(value) % 2))


def scan_black_bars(
    gray: np.ndarray,
    *,
    limit: float = DEFAULT_CROPDETECT_LIMIT,
) -> PixelCrop | None:
    """Return content crop inside a 2-D uint8 grayscale frame, or None if full-frame."""
    if gray.ndim != 2 or gray.size == 0:
        return None
    height, width = gray.shape
    if height < 4 or width < 4:
        return None
    thresh = max(1.0, min(255.0, float(limit)))
    row_mean = gray.mean(axis=1)
    col_mean = gray.mean(axis=0)
    content_rows = np.flatnonzero(row_mean > thresh)
    content_cols = np.flatnonzero(col_mean > thresh)
    if content_rows.size == 0 or content_cols.size == 0:
        return None
    y0 = int(content_rows[0])
    y1 = int(content_rows[-1]) + 1
    x0 = int(content_cols[0])
    x1 = int(content_cols[-1]) + 1
    # Snap to even dims for yuv420 friendliness when mapped back.
    y0 -= y0 % 2
    x0 -= x0 % 2
    h = y1 - y0
    w = x1 - x0
    h -= h % 2
    w -= w % 2
    if w < 2 or h < 2:
        return None
    if w >= width * 0.98 and h >= height * 0.98:
        return None
    return PixelCrop(width=w, height=h, x=x0, y=y0)


def _is_bar_like_crop(crop: PixelCrop, frame_w: int, frame_h: int) -> bool:
    """Keep letterbox (near-full width) / pillarbox (near-full height) only."""
    full_w = crop.width >= frame_w * 0.92
    full_h = crop.height >= frame_h * 0.92
    enough_w = crop.width >= frame_w * 0.40
    enough_h = crop.height >= frame_h * 0.40
    letterbox = full_w and enough_h and crop.height < frame_h * 0.98
    pillarbox = full_h and enough_w and crop.width < frame_w * 0.98
    return letterbox or pillarbox


def choose_stable_crop(
    crops: list[PixelCrop],
    *,
    frame_w: int,
    frame_h: int,
) -> PixelCrop | None:
    """Intersect sampled letterbox/pillarbox boxes (ignore tiny dark-scene blobs)."""
    if not crops or frame_w <= 0 or frame_h <= 0:
        return None
    bar_crops = [c for c in crops if _is_bar_like_crop(c, frame_w, frame_h)]
    if not bar_crops:
        return None
    # Map each crop into source pixel space if they were measured on a scaled frame
    # by treating them as already in the same coordinate system as frame_w/h.
    x0 = max(c.x for c in bar_crops)
    y0 = max(c.y for c in bar_crops)
    x1 = min(c.x + c.width for c in bar_crops)
    y1 = min(c.y + c.height for c in bar_crops)
    w = x1 - x0
    h = y1 - y0
    if w < 2 or h < 2:
        return None
    w -= w % 2
    h -= h % 2
    x0 -= x0 % 2
    y0 -= y0 % 2
    if w < 2 or h < 2:
        return None
    if w >= frame_w * 0.98 and h >= frame_h * 0.98:
        return None
    if x0 < 0 or y0 < 0 or x0 + w > frame_w + 2 or y0 + h > frame_h + 2:
        return None
    chosen = PixelCrop(width=w, height=h, x=x0, y=y0)
    if not _is_bar_like_crop(chosen, frame_w, frame_h):
        return None
    return chosen


def extract_gray_frame(
    media_path: Path,
    *,
    seek_s: float,
    frame_w: int,
    frame_h: int,
    ffmpeg: str | None = None,
    timeout_s: float = DETECT_TIMEOUT_S,
) -> np.ndarray | None:
    """Decode one grayscale frame scaled to ``ANALYZE_WIDTH`` (aspect preserved)."""
    if frame_w <= 0 or frame_h <= 0:
        return None
    exe = resolve_executable("ffmpeg") if ffmpeg is None else ffmpeg
    analyze_w = _even(min(ANALYZE_WIDTH, frame_w))
    analyze_h = _even(max(2, round(frame_h * analyze_w / frame_w)))
    cmd = [
        exe,
        "-hide_banner",
        "-loglevel",
        "error",
        "-ss",
        f"{max(0.0, seek_s):.3f}",
        "-i",
        str(media_path),
        "-an",
        "-map",
        "0:v:0",
        "-frames:v",
        "1",
        "-vf",
        f"scale={analyze_w}:{analyze_h}",
        "-f",
        "rawvideo",
        "-pix_fmt",
        "gray",
        "-",
    ]
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            timeout=timeout_s,
            check=False,
            **no_window_kwargs(),
        )
    except subprocess.TimeoutExpired:
        logger.warning("matte frame extract timed out near t=%.1fs", seek_s)
        return None
    raw = proc.stdout or b""
    expected = analyze_w * analyze_h
    if proc.returncode not in (0, None) or len(raw) < expected:
        logger.warning(
            "matte frame extract failed near t=%.1fs (exit %s, bytes=%s)",
            seek_s,
            proc.returncode,
            len(raw),
        )
        return None
    return np.frombuffer(raw[:expected], dtype=np.uint8).reshape((analyze_h, analyze_w))


def _scale_crop_to_source(
    crop: PixelCrop,
    *,
    analyze_w: int,
    analyze_h: int,
    frame_w: int,
    frame_h: int,
) -> PixelCrop:
    sx = frame_w / max(1, analyze_w)
    sy = frame_h / max(1, analyze_h)
    x = int(round(crop.x * sx))
    y = int(round(crop.y * sy))
    w = int(round(crop.width * sx))
    h = int(round(crop.height * sy))
    x = max(0, x - (x % 2))
    y = max(0, y - (y % 2))
    w = max(2, w - (w % 2))
    h = max(2, h - (h % 2))
    w = min(w, frame_w - x)
    h = min(h, frame_h - y)
    w -= w % 2
    h -= h % 2
    return PixelCrop(width=max(2, w), height=max(2, h), x=x, y=y)


def detect_video_matte(
    media_path: Path,
    *,
    frame_w: int,
    frame_h: int,
    duration_s: float | None = None,
    limit: float = DEFAULT_CROPDETECT_LIMIT,
    ffmpeg: str | None = None,
    timeout_s: float = DETECT_TIMEOUT_S,
) -> dict[str, float] | None:
    """
    Sample mid-title frames and return a normalized content box.

    Returns None when no stable letterbox/pillarbox is found (full-frame content),
    or when frames could not be decoded.
    """
    if frame_w <= 0 or frame_h <= 0:
        return None
    exe = resolve_executable("ffmpeg") if ffmpeg is None else ffmpeg
    analyze_w = _even(min(ANALYZE_WIDTH, frame_w))
    analyze_h = _even(max(2, round(frame_h * analyze_w / frame_w)))
    source_crops: list[PixelCrop] = []
    decoded_any = False
    for seek_s in _sample_seek_times(duration_s):
        logger.info("Detecting video matte near t=%.1fs: %s", seek_s, media_path.name)
        gray = extract_gray_frame(
            media_path,
            seek_s=seek_s,
            frame_w=frame_w,
            frame_h=frame_h,
            ffmpeg=exe,
            timeout_s=timeout_s,
        )
        if gray is None:
            continue
        decoded_any = True
        crop = scan_black_bars(gray, limit=limit)
        if crop is None:
            continue
        source_crops.append(
            _scale_crop_to_source(
                crop,
                analyze_w=analyze_w,
                analyze_h=analyze_h,
                frame_w=frame_w,
                frame_h=frame_h,
            )
        )

    if not decoded_any:
        return None
    chosen = choose_stable_crop(source_crops, frame_w=frame_w, frame_h=frame_h)
    if chosen is None:
        return None
    box = chosen.as_normalized(frame_w, frame_h)
    if box["width"] >= 0.98 and box["height"] >= 0.98:
        return None
    return box
