"""Cooperative cancellation for background jobs."""

from __future__ import annotations

import threading


class JobCancelled(Exception):
    """Raised when a worker job is cancelled mid-flight."""


def check_cancelled(cancel_event: threading.Event | None) -> None:
    if cancel_event is not None and cancel_event.is_set():
        raise JobCancelled()
