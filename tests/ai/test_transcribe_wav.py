"""WAV ingest for Whisper without going through PyAV."""

from __future__ import annotations

import math
import struct
import sys
import wave
from pathlib import Path
from types import SimpleNamespace

import pytest

from corrcut.ai.transcribe import WHISPER_SAMPLE_RATE, load_wav_mono_f32, transcribe_words


def _write_sine_wav(path: Path, *, seconds: float = 0.25, freq: float = 440.0) -> None:
    n = int(WHISPER_SAMPLE_RATE * seconds)
    with wave.open(str(path), "w") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(WHISPER_SAMPLE_RATE)
        frames = b"".join(
            struct.pack(
                "<h",
                int(8000 * math.sin(2 * math.pi * freq * i / WHISPER_SAMPLE_RATE)),
            )
            for i in range(n)
        )
        handle.writeframes(frames)


def test_load_wav_mono_f32_returns_unit_scale_float32(tmp_path: Path) -> None:
    pytest.importorskip("numpy")
    import numpy as np

    path = tmp_path / "tone.wav"
    _write_sine_wav(path)
    audio = load_wav_mono_f32(path)
    assert isinstance(audio, np.ndarray)
    assert audio.dtype == np.float32
    assert audio.ndim == 1
    assert len(audio) == int(WHISPER_SAMPLE_RATE * 0.25)
    assert float(np.max(np.abs(audio))) <= 1.0 + 1e-5


def test_load_wav_mono_f32_rejects_stereo(tmp_path: Path) -> None:
    path = tmp_path / "stereo.wav"
    with wave.open(str(path), "w") as handle:
        handle.setnchannels(2)
        handle.setsampwidth(2)
        handle.setframerate(WHISPER_SAMPLE_RATE)
        handle.writeframes(b"\0\0\0\0" * 64)
    with pytest.raises(ValueError, match="mono"):
        load_wav_mono_f32(path)


def test_transcribe_words_passes_numpy_audio_not_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Regression: path-based transcribe hits broken PyAV 19 + faster-whisper."""
    pytest.importorskip("numpy")
    import numpy as np

    path = tmp_path / "tone.wav"
    _write_sine_wav(path)
    captured: dict[str, object] = {}

    class _FakeModel:
        def __init__(self, *_args: object, **_kwargs: object) -> None:
            pass

        def transcribe(self, audio: object, **kwargs: object) -> tuple[list[object], object]:
            captured["audio"] = audio
            captured["kwargs"] = kwargs
            return [], object()

    monkeypatch.setitem(sys.modules, "faster_whisper", SimpleNamespace(WhisperModel=_FakeModel))

    words = transcribe_words(path, model_size="tiny", language="en")
    assert words == []
    audio = captured["audio"]
    assert isinstance(audio, np.ndarray)
    assert audio.dtype == np.float32
    assert not isinstance(audio, (str, Path))
    kwargs = captured["kwargs"]
    assert isinstance(kwargs, dict)
    assert kwargs.get("word_timestamps") is True
    assert kwargs.get("vad_filter") is True
