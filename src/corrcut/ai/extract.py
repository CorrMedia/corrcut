"""Extract mono 16 kHz audio for Whisper from the dialogue target."""

from __future__ import annotations

import subprocess
from pathlib import Path

from corrcut.domain.dialogue_target import DialogueTargetStrategy
from corrcut.engine.hidden_process import no_window_kwargs
from corrcut.engine.jobs.cancel import check_cancelled
from corrcut.engine.toolchain import resolve_executable


def transcription_audio_filter(
    strategy: DialogueTargetStrategy,
    channel_layout: str | None,
) -> str:
    if strategy == DialogueTargetStrategy.CENTER_CHANNEL:
        return "pan=mono|c0=FC"
    if channel_layout and "mono" in channel_layout.lower():
        return "anull"
    return "pan=mono|c0=0.5*FL+0.5*FR"


def extract_transcription_wav(
    source_path: Path,
    stream_index: int,
    output_wav: Path,
    *,
    strategy: DialogueTargetStrategy,
    channel_layout: str | None,
    ffmpeg: str | None = None,
    cancel_event: object | None = None,
) -> None:
    """Decode selected stream to mono 16 kHz PCM WAV (Whisper input)."""
    check_cancelled(cancel_event)  # type: ignore[arg-type]
    exe = resolve_executable("ffmpeg") if ffmpeg is None else ffmpeg
    af = transcription_audio_filter(strategy, channel_layout)
    cmd = [
        exe,
        "-hide_banner",
        "-y",
        "-i",
        str(source_path.resolve()),
        "-map",
        f"0:{stream_index}",
        "-af",
        af,
        "-ar",
        "16000",
        "-ac",
        "1",
        "-c:a",
        "pcm_s16le",
        str(output_wav.resolve()),
    ]
    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=7200,
        check=False,
        **no_window_kwargs(),
    )
    check_cancelled(cancel_event)  # type: ignore[arg-type]
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "ffmpeg extract failed").strip()
        raise RuntimeError(detail)
    if not output_wav.is_file():
        raise RuntimeError("ffmpeg produced no transcription WAV")
