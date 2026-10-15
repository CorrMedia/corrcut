"""Bounded job queue with one active subprocess worker (5d)."""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from typing import Any

from PySide6.QtCore import QEventLoop, QObject, QThread, Signal
from PySide6.QtWidgets import QApplication

from corrcut.engine.jobs.callable_job import CallableJobWorker
from corrcut.engine.jobs.subprocess_job import JobSpec, SubprocessJobWorker
from corrcut.engine.toolchain import resolve_executable

logger = logging.getLogger(__name__)


class JobRunner(QObject):
    """
    Owns subprocess jobs on dedicated QThreads.

    v1: one active job at a time; additional jobs are rejected while busy.
    """

    job_started = Signal(str)
    job_progress = Signal(int, str)
    job_log = Signal(str)
    job_finished = Signal(int)
    job_result = Signal(object)
    job_error = Signal(str)
    job_cancelled = Signal()
    busy_changed = Signal(bool)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._busy = False
        self._active_label = ""
        self._thread: QThread | None = None
        self._worker: QObject | None = None
        self._cancel_event = threading.Event()

    @property
    def cancel_event(self) -> threading.Event:
        return self._cancel_event

    @property
    def is_busy(self) -> bool:
        return self._busy

    @property
    def is_thread_running(self) -> bool:
        thread = self._thread
        return thread is not None and thread.isRunning()

    @property
    def active_label(self) -> str:
        """Label of the running job, or empty when idle."""
        return self._active_label if self._busy else ""

    def run_job(self, spec: JobSpec) -> bool:
        if self.is_busy:
            logger.warning("JobRunner busy; rejecting job %s", spec.label)
            return False

        self._cancel_event.clear()
        self._busy = True
        self._active_label = spec.label
        self.busy_changed.emit(True)
        self.job_started.emit(spec.label)

        worker = SubprocessJobWorker(spec)
        thread = QThread()
        worker.moveToThread(thread)

        thread.started.connect(worker.run)
        worker.progress.connect(self.job_progress.emit)
        worker.log_line.connect(self.job_log.emit)
        worker.finished_ok.connect(self._on_finished)
        worker.failed.connect(self._relay_worker_failed)
        worker.cancelled.connect(self._on_cancelled)
        worker.finished_ok.connect(thread.quit)
        worker.failed.connect(thread.quit)
        worker.cancelled.connect(thread.quit)
        thread.finished.connect(lambda w=worker, t=thread: self._destroy_worker_thread(w, t))

        self._thread = thread
        self._worker = worker
        thread.start()
        return True

    def run_callable(self, label: str, fn: Callable[[], Any]) -> bool:
        if self.is_busy:
            logger.warning("JobRunner busy; rejecting callable %s", label)
            return False

        self._cancel_event.clear()
        self._busy = True
        self._active_label = label
        self.busy_changed.emit(True)
        self.job_started.emit(label)

        worker = CallableJobWorker(label, fn, self._cancel_event)
        thread = QThread()
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.log_line.connect(self.job_log.emit)
        worker.finished_ok.connect(self._on_callable_finished)
        worker.failed.connect(self._relay_worker_failed)
        worker.cancelled.connect(self._on_cancelled)
        worker.finished_ok.connect(thread.quit)
        worker.failed.connect(thread.quit)
        worker.cancelled.connect(thread.quit)
        thread.finished.connect(lambda w=worker, t=thread: self._destroy_worker_thread(w, t))

        self._thread = thread
        self._worker = worker
        thread.start()
        return True

    def cancel_active(self) -> None:
        self._cancel_event.set()
        if self._worker is not None and hasattr(self._worker, "request_cancel"):
            self._worker.request_cancel()  # type: ignore[union-attr]

    def request_stop(self) -> None:
        """Cancel the active job and ask its thread to quit without waiting."""
        self.cancel_active()
        thread = self._thread
        if thread is None:
            self._set_idle()
            return
        if thread.isRunning():
            thread.quit()
        else:
            self._clear_active_refs()
            self._set_idle()

    def force_stop(self, *, wait_ms: int = 10_000, pump_events: bool = False) -> bool:
        """
        Cancel the active job and wait for its QThread to exit.

        Kills subprocess jobs when supported. Returns True when no worker thread is running.
        When *pump_events* is True, the GUI event loop is pumped between short waits
        so a closing notice can keep painting.
        """
        self.request_stop()
        thread = self._thread
        if thread is None:
            self._set_idle()
            return True
        if thread.isRunning():
            self._wait_for_thread(thread, wait_ms=wait_ms, pump_events=pump_events)
            if thread.isRunning():
                logger.warning("Job thread did not exit within %d ms; terminating", wait_ms)
                thread.terminate()
                self._wait_for_thread(thread, wait_ms=2_000, pump_events=pump_events)
        stopped = not thread.isRunning()
        if stopped:
            self._clear_active_refs()
            self._set_idle()
        return stopped

    def _wait_for_thread(
        self,
        thread: QThread,
        *,
        wait_ms: int,
        pump_events: bool,
    ) -> None:
        if wait_ms <= 0:
            return
        if not pump_events:
            thread.wait(wait_ms)
            return
        deadline = time.monotonic() + (wait_ms / 1000.0)
        while thread.isRunning() and time.monotonic() < deadline:
            remaining_ms = max(1, int((deadline - time.monotonic()) * 1000))
            if thread.wait(min(50, remaining_ms)):
                return
            app = QApplication.instance()
            if app is not None:
                app.processEvents(QEventLoop.ProcessEventsFlag.ExcludeUserInputEvents)

    def _destroy_worker_thread(self, worker: QObject, thread: QThread) -> None:
        """Delete worker/thread only after the thread event loop has exited."""
        if self._thread is thread:
            self._clear_active_refs()
            self._set_idle()
        worker.deleteLater()
        thread.deleteLater()

    def _clear_active_refs(self) -> None:
        self._thread = None
        self._worker = None

    def shutdown(self, *, wait_ms: int = 10_000, pump_events: bool = False) -> None:
        """Stop the worker thread; call from closeEvent (may block briefly)."""
        self.force_stop(wait_ms=wait_ms, pump_events=pump_events)

    def _set_idle(self) -> None:
        self._busy = False
        self._active_label = ""
        self.busy_changed.emit(False)

    def _on_finished(self, code: int) -> None:
        self.job_finished.emit(code)

    def _on_callable_finished(self, result: object) -> None:
        self.job_result.emit(result)

    def _relay_worker_failed(self, message: str) -> None:
        self.job_error.emit(message)

    def _on_cancelled(self) -> None:
        self.job_cancelled.emit()


def ffmpeg_version_job_spec(ffmpeg_path: str | None = None) -> JobSpec:
    """Harmless job for testing JobRunner — prints ffmpeg version."""
    exe = resolve_executable("ffmpeg") if ffmpeg_path is None else ffmpeg_path
    return JobSpec(
        command=[exe, "-version"],
        label="ffmpeg -version",
    )


def ffmpeg_progress_demo_spec(
    ffmpeg_path: str | None = None,
    duration_seconds: float = 2.0,
) -> JobSpec:
    """
    Generate silent audio with -progress pipe for cancel/progress testing.

    Requires ffmpeg on PATH.
    """
    exe = resolve_executable("ffmpeg") if ffmpeg_path is None else ffmpeg_path
    return JobSpec(
        command=[
            exe,
            "-hide_banner",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "anullsrc=r=48000:cl=stereo",
            "-t",
            str(duration_seconds),
            "-f",
            "null",
            "-progress",
            "pipe:1",
            "-",
        ],
        label="ffmpeg progress demo",
    )
