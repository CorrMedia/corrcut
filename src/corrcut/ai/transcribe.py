"""faster-whisper transcription with word timestamps."""

from __future__ import annotations

import logging
import wave
from collections.abc import Callable
from pathlib import Path

from corrcut.ai.language import whisper_language_code
from corrcut.ai.transcript import TranscriptWord
from corrcut.domain.time_point import TimePoint

logger = logging.getLogger(__name__)

WHISPER_SAMPLE_RATE = 16000


def is_whisper_available() -> bool:
    """Report whether faster-whisper is installed.

    Uses :func:`importlib.util.find_spec` instead of importing the package so
    the check is safe to run in the main process. Importing ``faster_whisper``
    eagerly loads ``ctranslate2``'s native DLLs, which crash with
    ``OSError: [WinError 127]`` on Windows when PaddlePaddle (bitmap OCR) has
    already loaded a conflicting Intel OpenMP/MKL runtime into the process.
    Actual transcription runs in an isolated subprocess (see
    :func:`corrcut.ai.pipeline.run_detection`), so a spec check is the
    correct signal for "installed" here.
    """
    import importlib.util  # noqa: PLC0415

    return importlib.util.find_spec("faster_whisper") is not None


def whisper_install_hint() -> str:
    return 'Install AI extras: pip install -e ".[ai]"'


def load_wav_mono_f32(wav_path: Path) -> object:
    """Load a mono PCM WAV as float32 samples in ``[-1, 1]``.

    faster-whisper accepts a NumPy array and skips its PyAV ``decode_audio``
    path. That matters on Windows installs: PyAV 19 dropped
    ``metadata_errors=``, which faster-whisper 1.2.x still passes, so every
    path-based ``model.transcribe(...)`` call raises ``TypeError``.
    """
    import numpy as np

    with wave.open(str(wav_path), "rb") as handle:
        channels = handle.getnchannels()
        sample_width = handle.getsampwidth()
        frame_rate = handle.getframerate()
        frames = handle.readframes(handle.getnframes())

    if channels != 1:
        raise ValueError(f"expected mono WAV, got {channels} channel(s)")
    if sample_width != 2:
        raise ValueError(f"expected 16-bit PCM WAV, got sample_width={sample_width}")
    if frame_rate != WHISPER_SAMPLE_RATE:
        raise ValueError(f"expected {WHISPER_SAMPLE_RATE} Hz WAV, got frame_rate={frame_rate}")

    return np.frombuffer(frames, dtype=np.int16).astype(np.float32) / 32768.0


def transcribe_words(
    wav_path: Path,
    *,
    model_size: str = "base",
    language: str | None = None,
    sample_rate: int = WHISPER_SAMPLE_RATE,
    progress: Callable[[int, str], None] | None = None,
    cancel_event: object | None = None,
) -> list[TranscriptWord]:
    """
    Transcribe audio with word-level timestamps.

    Timestamps are quantized to ``sample_rate`` on ingest (blueprint 5c).
    """
    from faster_whisper import WhisperModel

    from corrcut.engine.jobs.cancel import check_cancelled

    if progress is not None:
        progress(12, f"Loading Whisper model ({model_size})…")

    model = WhisperModel(model_size, device="cpu", compute_type="int8")

    if progress is not None:
        progress(18, "Transcribing…")

    whisper_lang = whisper_language_code(language)
    audio = load_wav_mono_f32(wav_path)
    segments, _info = model.transcribe(
        audio,
        language=whisper_lang,
        word_timestamps=True,
        vad_filter=True,
    )

    words: list[TranscriptWord] = []
    segment_list = list(segments)
    total = max(1, len(segment_list))

    for idx, segment in enumerate(segment_list):
        check_cancelled(cancel_event)  # type: ignore[arg-type]
        if progress is not None:
            pct = 18 + int(72 * (idx + 1) / total)
            progress(pct, f"Transcribing segment {idx + 1}/{total}…")

        segment_words = segment.words or []
        if not segment_words:
            text = (segment.text or "").strip()
            if text:
                words.append(
                    TranscriptWord(
                        text=text,
                        start=TimePoint.from_seconds_float(segment.start, sample_rate),
                        end=TimePoint.from_seconds_float(segment.end, sample_rate),
                        probability=0.5,
                        segment_text=text,
                    )
                )
            continue

        seg_text = (segment.text or "").strip()
        for word in segment_words:
            token = (word.word or "").strip()
            if not token:
                continue
            prob = float(word.probability) if word.probability is not None else 0.5
            words.append(
                TranscriptWord(
                    text=token,
                    start=TimePoint.from_seconds_float(word.start, sample_rate),
                    end=TimePoint.from_seconds_float(word.end, sample_rate),
                    probability=max(0.0, min(1.0, prob)),
                    segment_text=seg_text,
                )
            )

    if progress is not None:
        progress(92, f"Transcribed {len(words)} words")

    return words
