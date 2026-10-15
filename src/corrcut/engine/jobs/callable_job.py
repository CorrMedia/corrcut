"""Run Python callables on a worker QThread (waveform build, etc.)."""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from typing import Any

from PySide6.QtCore import QObject, Signal

from corrcut.engine.jobs.cancel import JobCancelled

logger = logging.getLogger(__name__)


class CallableJobWorker(QObject):
    finished_ok = Signal(object)
    failed = Signal(str)
    cancelled = Signal()
    log_line = Signal(str)

    def __init__(
        self,
        label: str,
        fn: Callable[[], Any],
        cancel_event: threading.Event | None = None,
    ) -> None:
        super().__init__()
        self._label = label
        self._fn = fn
        self._cancel_event = cancel_event
        self._cancel_requested = False

    def request_cancel(self) -> None:
        self._cancel_requested = True
        if self._cancel_event is not None:
            self._cancel_event.set()

    def run(self) -> None:
        if self._cancel_requested or self._is_cancelled():
            self.cancelled.emit()
            return
        self.log_line.emit(f"Starting: {self._label}")
        try:
            result = self._fn()
            if self._cancel_requested or self._is_cancelled():
                self.cancelled.emit()
                return
            self.finished_ok.emit(result)
        except JobCancelled:
            self.cancelled.emit()
        except Exception as exc:
            logger.exception("Callable job failed: %s", self._label)
            self.failed.emit(str(exc))

    def _is_cancelled(self) -> bool:
        return self._cancel_event is not None and self._cancel_event.is_set()
