"""Background job cancellation helpers."""

from __future__ import annotations

import threading
import time

import pytest

from corrcut.engine.jobs.cancel import JobCancelled, check_cancelled
from corrcut.engine.waveform import _stream_ffmpeg_peaks


def test_check_cancelled_raises_when_set() -> None:
    event = threading.Event()
    event.set()
    with pytest.raises(JobCancelled):
        check_cancelled(event)


def test_stream_ffmpeg_peaks_honours_cancel() -> None:
    cancel = threading.Event()
    cmd = [
        "ffmpeg",
        "-hide_banner",
        "-re",
        "-f",
        "lavfi",
        "-i",
        "anullsrc=r=48000:cl=stereo",
        "-t",
        "30",
        "-f",
        "f32le",
        "-",
    ]

    def _cancel_soon() -> None:
        time.sleep(0.2)
        cancel.set()

    threading.Thread(target=_cancel_soon, daemon=True).start()
    with pytest.raises(JobCancelled):
        _stream_ffmpeg_peaks(
            cmd,
            target_peaks=64,
            samples_per_peak=1_000_000,
            cancel_event=cancel,
            timeout_s=60.0,
        )
