"""Cancellable subprocess runner."""

from __future__ import annotations

import sys
import threading

import pytest

from corrcut.engine.jobs.cancel import JobCancelled
from corrcut.engine.jobs.run_process import run_process


def test_run_process_cancel_kills_child() -> None:
    cancel = threading.Event()

    def _cancel_soon() -> None:
        cancel.set()

    timer = threading.Timer(0.2, _cancel_soon)
    timer.start()
    try:
        with pytest.raises(JobCancelled):
            run_process(
                [sys.executable, "-c", "import time; time.sleep(30)"],
                cancel_event=cancel,
                timeout_s=10,
            )
    finally:
        timer.cancel()
