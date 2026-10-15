"""Multi-resolution waveform peak cache."""

from __future__ import annotations

import concurrent.futures
import contextlib
import json
import struct
import subprocess
import threading
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from corrcut.domain.channel_layout import (
    default_layout_for_channels,
    known_layout_channel_labels,
)
from corrcut.domain.dialogue_target import DialogueTargetStrategy
from corrcut.domain.project import TrackRecord
from corrcut.engine.hidden_process import no_window_kwargs
from corrcut.engine.jobs.cancel import check_cancelled
from corrcut.engine.jobs.subprocess_job import JobSpec
from corrcut.engine.toolchain import resolve_executable

PEAK_MAGIC = b"MSPK1"
PEAK_HEADER_SIZE = len(PEAK_MAGIC)
LEVELS = 8
BASE_PEAKS_PER_SECOND = 200
# Decode below native rate for the envelope — peaks do not need 48 kHz PCM.
WAVEFORM_DECODE_RATE = 12_000
# Parallel ffmpeg slices; 4 is a good trade against filmstrip disk use.
WAVEFORM_PARALLEL_SEGMENTS = 4
# Emit UI progress every ~0.2 s of audio so the ribbon moves sooner.
_PROGRESS_PEAKS = max(1, BASE_PEAKS_PER_SECOND // 5)
# One chunk is enough to reduce many peak windows; the rest of the PCM is never stored.
_PCM_CHUNK_BYTES = 1 << 20

PeakChunkCallback = Callable[[int, np.ndarray], None]


@dataclass(frozen=True)
class PeakIndex:
    levels: int
    peaks_per_second_base: int
    sample_rate: int
    duration_seconds: float
    channel_key: str


@dataclass
class PeakStore:
    """On-disk mipmap min/max peaks for timeline rendering."""

    index_path: Path
    data_path: Path
    index: PeakIndex
    _memory_levels: dict[int, list[tuple[float, float]]] = field(
        default_factory=dict, repr=False, compare=False
    )
    _ready_mask: np.ndarray | None = field(default=None, repr=False, compare=False)
    _base_array: np.ndarray | None = field(default=None, repr=False, compare=False)
    _ready_count: int = field(default=0, repr=False, compare=False)
    _building: bool = field(default=False, repr=False, compare=False)

    @property
    def is_building(self) -> bool:
        return self._building

    @property
    def ready_count(self) -> int:
        return int(self._ready_count)

    def has_displayable_peaks(self) -> bool:
        """True when at least one base peak is ready to draw."""
        if self._building:
            return self._ready_count > 0
        return 0 in self._memory_levels and bool(self._memory_levels[0])

    def base_peak_arrays(self) -> tuple[np.ndarray, np.ndarray] | None:
        """During a progressive build, return ``(peaks Nx2, ready mask)`` for painting."""
        if not self._building or self._base_array is None or self._ready_mask is None:
            return None
        return self._base_array, self._ready_mask

    def peak_ready(self, level: int, index: int) -> bool:
        """Whether a peak sample should be drawn (building stores track base readiness)."""
        if self._ready_mask is None:
            return True
        if level != 0:
            return False
        if index < 0 or index >= int(self._ready_mask.shape[0]):
            return False
        return bool(self._ready_mask[index])

    def peaks_for_level(self, level: int) -> list[tuple[float, float]]:
        """Return a preloaded level, loading from disk synchronously if needed (tests/tools)."""
        if level not in self._memory_levels:
            self._memory_levels[level] = self.read_level_full(level)
        return self._memory_levels[level]

    def peaks_for_level_if_ready(self, level: int) -> list[tuple[float, float]] | None:
        """Return peaks only when already in RAM (safe for UI paint; no disk I/O)."""
        if self._building:
            # Prefer ``base_peak_arrays()`` while building — avoids a huge Python list.
            return None
        return self._memory_levels.get(level)

    def read_level_full(self, level: int) -> list[tuple[float, float]]:
        total = self.total_peaks_at_level(level)
        return self.read_level_slice(level, 0, total)

    @classmethod
    def cache_dir(
        cls, cache_root: Path, fingerprint: dict[str, object], stream_index: int, channel_key: str
    ) -> Path:
        key = f"{fingerprint.get('head_sha256', 'x')}_{stream_index}_{channel_key}"
        return cache_root / "waveforms" / key

    @classmethod
    def open_or_none(
        cls, cache_root: Path, fingerprint: dict[str, object], stream_index: int, channel_key: str
    ) -> PeakStore | None:
        base = cls.cache_dir(cache_root, fingerprint, stream_index, channel_key)
        index_path = base / "index.json"
        data_path = base / "peaks.bin"
        if not index_path.is_file() or not data_path.is_file():
            return None
        meta = json.loads(index_path.read_text(encoding="utf-8"))
        index = PeakIndex(
            levels=int(meta["levels"]),
            peaks_per_second_base=int(meta["peaks_per_second_base"]),
            sample_rate=int(meta["sample_rate"]),
            duration_seconds=float(meta["duration_seconds"]),
            channel_key=str(meta["channel_key"]),
        )
        return cls(index_path=index_path, data_path=data_path, index=index)

    @classmethod
    def create_building(
        cls,
        cache_root: Path,
        fingerprint: dict[str, object],
        track: TrackRecord,
        *,
        duration_seconds: float,
        sample_rate: int | None = None,
    ) -> PeakStore:
        """Allocate an empty in-memory peak store that fills as decode progresses."""
        channel_key = _channel_key_for_track(track)
        base = cls.cache_dir(cache_root, fingerprint, track.stream_index, channel_key)
        base.mkdir(parents=True, exist_ok=True)
        index = PeakIndex(
            levels=LEVELS,
            peaks_per_second_base=BASE_PEAKS_PER_SECOND,
            sample_rate=int(sample_rate or track.sample_rate or 48000),
            duration_seconds=max(0.001, float(duration_seconds)),
            channel_key=channel_key,
        )
        store = cls(
            index_path=base / "index.json",
            data_path=base / "peaks.bin",
            index=index,
            _building=True,
        )
        total = store.total_peaks_at_level(0)
        store._ready_mask = np.zeros(total, dtype=bool)
        store._base_array = np.full((total, 2), np.nan, dtype=np.float32)
        store._ready_count = 0
        return store

    def apply_base_peak_slice(self, start_peak: int, peaks: np.ndarray) -> None:
        """Copy a contiguous base-level slice into memory (worker thread)."""
        if peaks.size == 0:
            return
        arr = np.ascontiguousarray(peaks, dtype=np.float32)
        if arr.ndim != 2 or arr.shape[1] != 2:
            raise ValueError("peaks must have shape (n, 2)")
        if self._base_array is None or self._ready_mask is None:
            total = self.total_peaks_at_level(0)
            self._base_array = np.full((total, 2), np.nan, dtype=np.float32)
            self._ready_mask = np.zeros(total, dtype=bool)
            self._ready_count = 0
        total = int(self._base_array.shape[0])
        start = max(0, int(start_peak))
        count = min(int(arr.shape[0]), total - start)
        if count <= 0:
            return
        end = start + count
        # Count newly covered peaks so overlapping writes do not inflate ready_count.
        newly = int(np.count_nonzero(~self._ready_mask[start:end]))
        self._base_array[start:end] = arr[:count]
        self._ready_mask[start:end] = True
        self._ready_count += newly

    def finalize_from_base(self, base_peaks: np.ndarray) -> None:
        """Write mipmaps to disk and replace in-memory levels with the complete pyramid."""
        arr = np.ascontiguousarray(base_peaks, dtype=np.float32)
        if arr.ndim != 2 or arr.shape[1] != 2:
            raise ValueError("base_peaks must have shape (n, 2)")
        levels_data: list[np.ndarray] = [arr]
        current = arr
        for _ in range(1, self.index.levels):
            current = _downsample_peaks(current)
            levels_data.append(current)

        self.data_path.parent.mkdir(parents=True, exist_ok=True)
        with self.data_path.open("wb") as handle:
            handle.write(PEAK_MAGIC)
            for level_peaks in levels_data:
                handle.write(np.ascontiguousarray(level_peaks, dtype="<f4").tobytes())
        self.index_path.write_text(
            json.dumps(
                {
                    "levels": self.index.levels,
                    "peaks_per_second_base": self.index.peaks_per_second_base,
                    "sample_rate": self.index.sample_rate,
                    "duration_seconds": self.index.duration_seconds,
                    "channel_key": self.index.channel_key,
                },
                indent=2,
            ),
            encoding="utf-8",
        )
        self._memory_levels = {
            level: [(float(row[0]), float(row[1])) for row in level_peaks]
            for level, level_peaks in enumerate(levels_data)
        }
        self._ready_mask = None
        self._base_array = None
        self._ready_count = int(arr.shape[0])
        self._building = False

    def level_peaks_per_second(self, level: int) -> int:
        divisor: int = 2**level
        return max(1, self.index.peaks_per_second_base // divisor)

    def total_peaks_at_level(self, level: int) -> int:
        return max(1, int(self.index.duration_seconds * self.level_peaks_per_second(level)))

    def read_level_slice(self, level: int, start_peak: int, count: int) -> list[tuple[float, float]]:
        offset = self._level_byte_offset(level)
        record_size = 8
        out: list[tuple[float, float]] = []
        with self.data_path.open("rb") as handle:
            handle.seek(offset + start_peak * record_size)
            raw = handle.read(count * record_size)
        for i in range(0, len(raw), record_size):
            if i + record_size > len(raw):
                break
            mn, mx = struct.unpack_from("<ff", raw, i)
            out.append((mn, mx))
        return out

    def _level_byte_offset(self, level: int) -> int:
        """Byte offset into peaks.bin for the start of a mipmap level (after file header)."""
        offset = PEAK_HEADER_SIZE
        for lvl in range(self.index.levels):
            n = self.total_peaks_at_level(lvl)
            if lvl == level:
                return offset
            offset += n * 8
        return offset

    @classmethod
    def build(
        cls,
        media_path: Path,
        cache_root: Path,
        fingerprint: dict[str, object],
        track: TrackRecord,
        ffmpeg: str | None = None,
        *,
        cancel_event: threading.Event | None = None,
        on_chunk: PeakChunkCallback | None = None,
        store: PeakStore | None = None,
    ) -> PeakStore:
        channel_key = _channel_key_for_track(track)
        track_rate = track.sample_rate or 48000
        check_cancelled(cancel_event)
        if store is None:
            duration = _probe_duration(media_path, ffmpeg, cancel_event=cancel_event) or 1.0
            check_cancelled(cancel_event)
            store = cls.create_building(
                cache_root,
                fingerprint,
                track,
                duration_seconds=duration,
                sample_rate=track_rate,
            )
        else:
            # UI already has probe duration; skip a second ffprobe round-trip.
            duration = store.index.duration_seconds

        afilter = _audio_filter_for_track(track, channel_key)
        ffmpeg_exe = resolve_executable("ffmpeg") if ffmpeg is None else ffmpeg
        total = store.total_peaks_at_level(0)
        decode_rate = WAVEFORM_DECODE_RATE
        samples_per_peak = _samples_per_peak(duration, decode_rate, total)
        base_peaks = np.zeros((total, 2), dtype=np.float32)
        lock = threading.Lock()
        last_ui_notify = [0.0]
        ui_notify_interval_s = 0.12

        def _absorb(abs_start: int, chunk: np.ndarray) -> None:
            if chunk.size == 0:
                return
            with lock:
                end = min(total, abs_start + int(chunk.shape[0]))
                count = end - abs_start
                if count <= 0:
                    return
                base_peaks[abs_start:end] = chunk[:count]
                store.apply_base_peak_slice(abs_start, chunk[:count])
            if on_chunk is None:
                return
            # Keep decode moving; do not flood the UI thread with chunk signals.
            now = time.monotonic()
            if (now - last_ui_notify[0]) < ui_notify_interval_s:
                return
            last_ui_notify[0] = now
            on_chunk(abs_start, chunk[:count].copy())

        segments = _split_peak_segments(total, WAVEFORM_PARALLEL_SEGMENTS)
        check_cancelled(cancel_event)
        with concurrent.futures.ThreadPoolExecutor(max_workers=len(segments)) as pool:
            futures = [
                pool.submit(
                    _decode_peak_segment,
                    media_path=media_path,
                    stream_index=track.stream_index,
                    ffmpeg_exe=ffmpeg_exe,
                    afilter=afilter,
                    decode_rate=decode_rate,
                    duration_seconds=duration,
                    total_peaks=total,
                    start_peak=start_peak,
                    peak_count=peak_count,
                    samples_per_peak=samples_per_peak,
                    cancel_event=cancel_event,
                    on_progress=_absorb,
                )
                for start_peak, peak_count in segments
            ]
            for fut in concurrent.futures.as_completed(futures):
                fut.result()

        check_cancelled(cancel_event)
        store.finalize_from_base(base_peaks)
        return store


def waveform_build_job_spec(
    media_path: Path,
    cache_root: Path,
    fingerprint: dict[str, object],
    track: TrackRecord,
    ffmpeg: str | None = None,
) -> JobSpec:
    """Job that builds peaks — run via dedicated worker callable, not raw ffmpeg progress."""
    # We use a Python-side job by shelling a small script; simpler: label only for UI.
    ffmpeg_exe = resolve_executable("ffmpeg") if ffmpeg is None else ffmpeg
    channel_key = _channel_key_for_track(track)
    return JobSpec(
        command=[
            ffmpeg_exe,
            "-version",
        ],
        label=f"waveform cache (stream {track.stream_index}, {channel_key})",
    )


def store_needs_preload(store: PeakStore) -> bool:
    if store.is_building:
        return False
    return len(store._memory_levels) < store.index.levels  # noqa: SLF001


def preload_peak_memory(
    store: PeakStore,
    *,
    cancel_event: threading.Event | None = None,
) -> PeakStore:
    """Load all mipmap levels from disk into RAM (run on a worker thread)."""
    for level in range(store.index.levels):
        check_cancelled(cancel_event)
        if level not in store._memory_levels:
            store._memory_levels[level] = store.read_level_full(level)  # noqa: SLF001
    return store


def build_waveform_cache(
    media_path: Path,
    cache_root: Path,
    fingerprint: dict[str, object],
    track: TrackRecord,
    *,
    cancel_event: threading.Event | None = None,
    on_chunk: PeakChunkCallback | None = None,
    store: PeakStore | None = None,
) -> PeakStore:
    built = PeakStore.build(
        media_path,
        cache_root,
        fingerprint,
        track,
        cancel_event=cancel_event,
        on_chunk=on_chunk,
        store=store,
    )
    return preload_peak_memory(built, cancel_event=cancel_event)


def _split_peak_segments(total_peaks: int, segments: int = WAVEFORM_PARALLEL_SEGMENTS) -> list[tuple[int, int]]:
    """Return ``(start_peak, count)`` slices covering ``[0, total_peaks)``."""
    total = max(1, int(total_peaks))
    n = max(1, min(int(segments), total))
    if n == 1:
        return [(0, total)]
    out: list[tuple[int, int]] = []
    start = 0
    for i in range(n):
        end = total if i == n - 1 else (total * (i + 1)) // n
        count = end - start
        if count > 0:
            out.append((start, count))
        start = end
    return out or [(0, total)]


def _split_peak_halves(total_peaks: int) -> list[tuple[int, int]]:
    """Two-way split kept for older tests/callers."""
    return _split_peak_segments(total_peaks, 2)


def _peak_window_time(
    start_peak: int,
    peak_count: int,
    *,
    duration_seconds: float,
    total_peaks: int,
) -> tuple[float, float]:
    """Map a peak index range to ``(start_s, duration_s)`` for ffmpeg ``-ss`` / ``-t``."""
    total = max(1, int(total_peaks))
    start_s = (float(start_peak) / float(total)) * float(duration_seconds)
    end_peak = min(total, start_peak + max(0, peak_count))
    end_s = (float(end_peak) / float(total)) * float(duration_seconds)
    return start_s, max(0.001, end_s - start_s)


def _samples_per_peak(duration_seconds: float, sample_rate: int, target_peaks: int) -> int:
    """Samples in one base-level peak window.

    Fixed up front so ffmpeg stdout can be reduced per chunk. Matches the old
    full-buffer window when the decode length is ``round(duration * sample_rate)``.
    """
    expected = max(1, int(round(float(duration_seconds) * int(sample_rate))))
    return max(1, expected // max(1, target_peaks))


class _PcmPeakReducer:
    """Running min/max over fixed sample windows. Holds one open window, not the PCM."""

    def __init__(
        self,
        target_peaks: int,
        samples_per_peak: int,
        *,
        on_progress: PeakChunkCallback | None = None,
        progress_every: int = _PROGRESS_PEAKS,
        progress_offset: int = 0,
    ) -> None:
        self._target = max(0, target_peaks)
        self._spp = max(1, samples_per_peak)
        self._peaks = np.zeros((self._target, 2), dtype=np.float32)
        self._filled = 0
        self._partial = 0
        self._partial_min = 0.0
        self._partial_max = 0.0
        self._pending = b""
        self._on_progress = on_progress
        self._progress_every = max(1, int(progress_every))
        self._progress_offset = int(progress_offset)
        self._last_emitted = 0
        self.done = self._target == 0

    def feed(self, chunk: bytes) -> None:
        if self.done or not chunk:
            return
        if self._pending:
            chunk = self._pending + chunk
            self._pending = b""
        usable = len(chunk) - (len(chunk) % 4)
        if usable:
            self._feed_samples(np.frombuffer(chunk[:usable], dtype="<f4"))
        if not self.done:
            self._pending = chunk[usable:]

    def finish(self) -> np.ndarray:
        if self._partial and self._filled < self._target:
            self._commit_partial()
        self.done = True
        self._emit_progress(force=True)
        return self._peaks

    def _feed_samples(self, samples: np.ndarray) -> None:
        if self.done or samples.size == 0:
            return
        offset = 0
        total = int(samples.size)
        if self._partial and self._filled < self._target:
            need = self._spp - self._partial
            take = min(need, total - offset)
            self._fold(samples[offset : offset + take])
            offset += take
            if self._partial == self._spp:
                self._commit_partial()
        if self._filled >= self._target:
            self.done = True
            self._emit_progress(force=True)
            return
        available = total - offset
        nwin = min(self._target - self._filled, available // self._spp)
        if nwin:
            end = offset + nwin * self._spp
            block = samples[offset:end].reshape(nwin, self._spp)
            self._peaks[self._filled : self._filled + nwin, 0] = block.min(axis=1)
            self._peaks[self._filled : self._filled + nwin, 1] = block.max(axis=1)
            self._filled += nwin
            offset = end
            self._emit_progress()
        if self._filled >= self._target:
            self.done = True
            self._emit_progress(force=True)
            return
        if offset < total:
            self._fold(samples[offset:])

    def _fold(self, block: np.ndarray) -> None:
        if block.size == 0:
            return
        bmin = float(block.min())
        bmax = float(block.max())
        if self._partial == 0:
            self._partial_min = bmin
            self._partial_max = bmax
        else:
            self._partial_min = min(self._partial_min, bmin)
            self._partial_max = max(self._partial_max, bmax)
        self._partial += int(block.size)

    def _commit_partial(self) -> None:
        self._peaks[self._filled, 0] = self._partial_min
        self._peaks[self._filled, 1] = self._partial_max
        self._filled += 1
        self._partial = 0
        self._emit_progress()

    def _emit_progress(self, *, force: bool = False) -> None:
        if self._on_progress is None:
            return
        if self._filled <= self._last_emitted:
            return
        if not force and (self._filled - self._last_emitted) < self._progress_every:
            return
        start = self._last_emitted
        chunk = self._peaks[start : self._filled].copy()
        self._last_emitted = self._filled
        self._on_progress(self._progress_offset + start, chunk)


def _reduce_pcm_chunks(
    chunks: Iterable[bytes],
    *,
    target_peaks: int,
    samples_per_peak: int,
) -> np.ndarray:
    reducer = _PcmPeakReducer(target_peaks, samples_per_peak)
    for chunk in chunks:
        reducer.feed(chunk)
        if reducer.done:
            break
    return reducer.finish()


def _decode_peak_segment(
    *,
    media_path: Path,
    stream_index: int,
    ffmpeg_exe: str,
    afilter: str | None,
    decode_rate: int,
    duration_seconds: float,
    total_peaks: int,
    start_peak: int,
    peak_count: int,
    samples_per_peak: int,
    cancel_event: threading.Event | None,
    on_progress: PeakChunkCallback | None,
) -> None:
    if peak_count <= 0:
        return
    start_s, dur_s = _peak_window_time(
        start_peak,
        peak_count,
        duration_seconds=duration_seconds,
        total_peaks=total_peaks,
    )
    # Input seek + one thread per process: we already parallelize across segments.
    cmd = [
        ffmpeg_exe,
        "-hide_banner",
        "-nostats",
        "-threads",
        "1",
        "-fflags",
        "+fastseek",
        "-ss",
        f"{start_s:.6f}",
        "-t",
        f"{dur_s:.6f}",
        "-i",
        str(media_path),
        "-map",
        f"0:{stream_index}",
        "-vn",
        "-sn",
        "-dn",
    ]
    if afilter:
        cmd.extend(["-af", afilter])
    cmd.extend(
        [
            "-ac",
            "1",
            "-ar",
            str(decode_rate),
            "-f",
            "f32le",
            "-",
        ]
    )
    _stream_ffmpeg_peaks(
        cmd,
        target_peaks=peak_count,
        samples_per_peak=samples_per_peak,
        cancel_event=cancel_event,
        on_progress=on_progress,
        progress_offset=start_peak,
    )


def _stream_ffmpeg_peaks(
    cmd: list[str],
    *,
    target_peaks: int,
    samples_per_peak: int,
    cancel_event: threading.Event | None = None,
    timeout_s: float = 600.0,
    on_progress: PeakChunkCallback | None = None,
    progress_offset: int = 0,
    progress_every: int = _PROGRESS_PEAKS,
) -> np.ndarray:
    """Decode ffmpeg f32le stdout in chunks and reduce each peak window with NumPy."""
    check_cancelled(cancel_event)
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        # Unbuffered so cancel does not stall on the stdout buffer lock.
        bufsize=0,
        **no_window_kwargs(),
    )
    stderr_parts: list[bytes] = []
    outcome: list[np.ndarray | BaseException] = []
    early_stop = False

    def _drain_stderr() -> None:
        stderr = proc.stderr
        if stderr is None:
            return
        try:
            stderr_parts.append(stderr.read())
        except OSError:
            return

    def _reduce_stdout() -> None:
        nonlocal early_stop
        try:
            reducer = _PcmPeakReducer(
                target_peaks,
                samples_per_peak,
                on_progress=on_progress,
                progress_every=progress_every,
                progress_offset=progress_offset,
            )
            if proc.stdout is None:
                raise RuntimeError("ffmpeg decode produced no output")
            while True:
                block = proc.stdout.read(_PCM_CHUNK_BYTES)
                if not block:
                    break
                reducer.feed(block)
                if reducer.done:
                    early_stop = True
                    break
            outcome.append(reducer.finish())
        except Exception as exc:
            outcome.append(exc)

    stderr_thread = threading.Thread(target=_drain_stderr, daemon=True)
    reader = threading.Thread(target=_reduce_stdout, daemon=True)
    stderr_thread.start()
    reader.start()
    deadline = time.monotonic() + timeout_s
    try:
        while reader.is_alive():
            check_cancelled(cancel_event)
            if time.monotonic() > deadline:
                raise RuntimeError("ffmpeg decode timed out")
            reader.join(timeout=0.05)
    except BaseException:
        _kill_ffmpeg(proc)
        reader.join(timeout=5)
        stderr_thread.join(timeout=5)
        raise

    reader.join(timeout=5)
    if early_stop and proc.poll() is None:
        _kill_ffmpeg(proc)
    else:
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            _kill_ffmpeg(proc)
    stderr_thread.join(timeout=5)

    if not outcome:
        raise RuntimeError("ffmpeg decode produced no output")
    result = outcome[0]
    if isinstance(result, BaseException):
        raise result
    if not early_stop and proc.returncode not in (0, None):
        stderr = b"".join(stderr_parts).decode(errors="replace")
        raise RuntimeError(stderr or "ffmpeg decode failed")
    return result


def _kill_ffmpeg(proc: subprocess.Popen[bytes]) -> None:
    if proc.poll() is None:
        proc.kill()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait(timeout=5)
    for pipe in (proc.stdout, proc.stderr):
        if pipe is not None:
            with contextlib.suppress(OSError, ValueError):
                pipe.close()


def _channel_key_for_track(track: TrackRecord) -> str:
    if track.dialogue_target_strategy != DialogueTargetStrategy.CENTER_CHANNEL:
        return "stereo" if _is_stereo_track(track) else "full"
    layout = track.channel_layout or default_layout_for_channels(track.channels)
    labels = known_layout_channel_labels(layout, track.channels)
    if labels is not None and "FC" in labels:
        return "FC"
    return "stereo" if _is_stereo_track(track) else "full"


def _is_stereo_track(track: TrackRecord) -> bool:
    layout = (track.channel_layout or "").strip().lower()
    if layout in {"stereo", "2.0"}:
        return True
    return int(track.channels or 0) == 2 and layout not in {"mono"}


def _audio_filter_for_track(track: TrackRecord, channel_key: str) -> str | None:
    if channel_key == "FC":
        return "pan=mono|c0=FC"
    if channel_key == "stereo" or _is_stereo_track(track):
        return "pan=mono|c0=0.5*FL+0.5*FR"
    return None


def _probe_duration(
    media_path: Path,
    ffmpeg: str | None,
    *,
    cancel_event: threading.Event | None = None,
) -> float | None:
    check_cancelled(cancel_event)
    from corrcut.engine.probe import probe_media

    # Use resolved ffprobe (bundled when present).
    result = probe_media(media_path)
    check_cancelled(cancel_event)
    return result.duration_seconds


def _downsample_peaks(peaks: np.ndarray) -> np.ndarray:
    count = int(peaks.shape[0])
    if count <= 1:
        return peaks
    out_count = (count + 1) // 2
    out = np.empty((out_count, 2), dtype=np.float32)
    pair_count = count // 2
    if pair_count:
        paired = np.ascontiguousarray(peaks[: pair_count * 2]).reshape(pair_count, 2, 2)
        out[:pair_count, 0] = paired[:, :, 0].min(axis=1)
        out[:pair_count, 1] = paired[:, :, 1].max(axis=1)
    if count % 2 == 1:
        out[-1] = peaks[-1]
    return out
