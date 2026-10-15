"""Subprocess helper that can be cancelled from a worker thread."""

from __future__ import annotations

import subprocess
import threading
import time
from typing import IO

from corrcut.engine.hidden_process import no_window_kwargs
from corrcut.engine.jobs.cancel import JobCancelled, check_cancelled


def run_process(
    cmd: list[str],
    *,
    cancel_event: threading.Event | None = None,
    timeout_s: float | None = None,
) -> subprocess.CompletedProcess[str]:
    """Run *cmd*, raising ``JobCancelled`` if *cancel_event* is set.

    stdout and stderr are read on side threads so a chatty ffmpeg cannot fill
    the pipe and stall.
    """
    check_cancelled(cancel_event)
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        **no_window_kwargs(),
    )
    stdout_chunks: list[str] = []
    stderr_chunks: list[str] = []
    stdout_done = threading.Event()
    stderr_done = threading.Event()
    timeout_limit = None if timeout_s is None else float(timeout_s)
    deadline = None if timeout_limit is None else time.monotonic() + timeout_limit

    def _read(stream: IO[str] | None, chunks: list[str], done: threading.Event) -> None:
        try:
            if stream is None:
                return
            for line in stream:
                chunks.append(line)
        finally:
            done.set()

    threading.Thread(
        target=_read, args=(proc.stdout, stdout_chunks, stdout_done), daemon=True
    ).start()
    threading.Thread(
        target=_read, args=(proc.stderr, stderr_chunks, stderr_done), daemon=True
    ).start()

    try:
        while True:
            if cancel_event is not None and cancel_event.is_set():
                _kill(proc)
                stdout_done.wait(timeout=5)
                stderr_done.wait(timeout=5)
                raise JobCancelled()
            if timeout_limit is not None and deadline is not None and time.monotonic() > deadline:
                _kill(proc)
                stdout_done.wait(timeout=5)
                stderr_done.wait(timeout=5)
                raise subprocess.TimeoutExpired(cmd, timeout_limit)
            if proc.poll() is not None and stdout_done.is_set() and stderr_done.is_set():
                break
            time.sleep(0.05)
    except JobCancelled:
        raise
    except subprocess.TimeoutExpired:
        raise
    except Exception:
        _kill(proc)
        raise

    code = proc.returncode if proc.returncode is not None else 1
    return subprocess.CompletedProcess(
        cmd,
        code,
        "".join(stdout_chunks),
        "".join(stderr_chunks),
    )


def _kill(proc: subprocess.Popen[str]) -> None:
    if proc.poll() is None:
        proc.kill()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        return
