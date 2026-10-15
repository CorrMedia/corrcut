"""Display and parse timeline timecodes (HH:MM:SS.mmm)."""

from __future__ import annotations

import re


def format_timecode(seconds: float) -> str:
    """Format seconds as HH:MM:SS.mmm for the timeline timecode field."""
    seconds = max(0.0, seconds)
    total_ms = int(round(seconds * 1000.0))
    ms = total_ms % 1000
    total_s = total_ms // 1000
    s = total_s % 60
    total_m = total_s // 60
    m = total_m % 60
    h = total_m // 60
    return f"{h:02d}:{m:02d}:{s:02d}.{ms:03d}"


def parse_timecode(text: str) -> float | None:
    """
    Parse a timecode or plain seconds string into seconds.

    Accepts: 12.5, 01:23, 01:23:45, 01:23:45.678, 01:23:45,678
    """
    raw = text.strip()
    if not raw:
        return None

    if re.fullmatch(r"\d+(\.\d+)?", raw):
        return float(raw)

    normalized = raw.replace(",", ".")
    parts = normalized.split(":")
    if len(parts) > 3:
        return None

    try:
        if len(parts) == 1:
            return float(parts[0])
        if len(parts) == 2:
            minutes = int(parts[0])
            seconds = float(parts[1])
            return minutes * 60.0 + seconds
        hours = int(parts[0])
        minutes = int(parts[1])
        seconds = float(parts[2])
        return hours * 3600.0 + minutes * 60.0 + seconds
    except ValueError:
        return None
