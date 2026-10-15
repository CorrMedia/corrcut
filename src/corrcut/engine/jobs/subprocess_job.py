"""QObject worker that runs one subprocess and emits Qt signals (5d)."""

from __future__ import annotations

import logging
import re
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import QObject, Signal

from corrcut.engine.hidden_process import no_window_kwargs

logger = logging.getLogger(__name__)

_PROGRESS_RE = re.compile(r"out_time_ms=(\d+)")
_DURATION_US_RE = re.compile(r"duration=(\d+)")


@dataclass(frozen=True)
class JobSpec:
    """Specification for a subprocess job."""

    command: list[str]
    label: str
    temp_dir: Path | None = None


class SubprocessJobWorker(QObject):
    """
    Runs a single subprocess on a dedicated QThread.

    Communicates to the UI only via signals — never touches widgets.
    """

    progress = Signal(int, str)  # percent 0-100, message
    log_line = Signal(str)
    finished_ok = Signal(int)  # exit code
    failed = Signal(str)
    cancelled = Signal()

    def __init__(self, spec: JobSpec) -> None:
        super().__init__()
        self._spec = spec
        self._process: subprocess.Popen[str] | None = None
        self._cancel_requested = False
        self._temp_dir_ctx: tempfile.TemporaryDirectory[str] | None = None

    def request_cancel(self) -> None:
        self._cancel_requested = True
        proc = self._process
        if proc is not None and proc.poll() is None:
            proc.terminate()

    def run(self) -> None:
        temp_path: Path | None = None
        if self._spec.temp_dir is not None:
            self._spec.temp_dir.mkdir(parents=True, exist_ok=True)
            temp_path = self._spec.temp_dir
        else:
            self._temp_dir_ctx = tempfile.TemporaryDirectory(prefix="ms_job_")
            temp_path = Path(self._temp_dir_ctx.name)

        try:
            self._run_subprocess(temp_path)
        finally:
            if self._temp_dir_ctx is not None:
                self._temp_dir_ctx.cleanup()

    def _run_subprocess(self, _temp_path: Path) -> None:
        cmd = self._spec.command
        logger.info("Starting job %s: %s", self._spec.label, cmd)
        self.log_line.emit(f"Running: {' '.join(cmd)}")

        try:
            self._process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
                **no_window_kwargs(),
            )
        except OSError as exc:
            self.failed.emit(str(exc))
            return

        duration_us: int | None = None
        tail_lines: list[str] = []
        assert self._process.stdout is not None

        for line in self._process.stdout:
            if self._cancel_requested:
                self._kill_process()
                self.cancelled.emit()
                return

            line = line.rstrip()
            if line:
                self.log_line.emit(line)
                tail_lines.append(line)
                if len(tail_lines) > 12:
                    tail_lines.pop(0)

            dur_match = _DURATION_US_RE.search(line)
            if dur_match:
                duration_us = int(dur_match.group(1))

            prog_match = _PROGRESS_RE.search(line)
            if prog_match and duration_us and duration_us > 0:
                out_us = int(prog_match.group(1))
                percent = min(100, int(out_us * 100 / duration_us))
                self.progress.emit(percent, self._spec.label)

        code = self._process.wait()
        self._process = None

        if self._cancel_requested:
            self.cancelled.emit()
            return

        if code == 0:
            self.progress.emit(100, self._spec.label)
            self.finished_ok.emit(code)
        else:
            signed = code - (1 << 32) if code > 0x7FFFFFFF else code
            detail = f"exit code {code}"
            if signed != code:
                detail += f" ({signed})"
            if tail_lines:
                detail += ": " + tail_lines[-1]
            self.failed.emit(detail)

    def _kill_process(self) -> None:
        proc = self._process
        if proc is None or proc.poll() is not None:
            return
        proc.terminate()
        try:
            proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            proc.kill()
