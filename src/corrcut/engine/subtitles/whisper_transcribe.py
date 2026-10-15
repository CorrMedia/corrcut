"""Whisper transcription for subtitle generation (no profanity detection)."""

from __future__ import annotations

import tempfile
from collections.abc import Callable
from pathlib import Path

from corrcut.ai.extract import extract_transcription_wav
from corrcut.ai.language import whisper_language_code
from corrcut.ai.transcribe import transcribe_words
from corrcut.ai.transcript import TranscriptWord
from corrcut.domain.project import Project
from corrcut.engine.jobs.cancel import check_cancelled


def transcribe_project_track(
    project: Project,
    *,
    model_size: str = "base",
    language: str | None = None,
    cancel_event: object | None = None,
    progress: Callable[[int, str], None] | None = None,
) -> list[TranscriptWord]:
    """Extract dialogue audio and transcribe with word timestamps."""
    if project.source is None:
        raise ValueError("project has no source media")
    track = project.selected_track()
    if track is None:
        raise ValueError("no audio track selected")

    source_path = project.source.path.resolve()
    whisper_lang = whisper_language_code(language or track.language)
    sample_rate = track.sample_rate or 16000

    if progress is not None:
        progress(0, "Extracting dialogue audio…")

    with tempfile.TemporaryDirectory(prefix="ms_sub_whisper_") as tmp:
        wav_path = Path(tmp) / "dialogue.wav"
        extract_transcription_wav(
            source_path,
            track.stream_index,
            wav_path,
            strategy=track.dialogue_target_strategy,
            channel_layout=track.channel_layout,
            cancel_event=cancel_event,
        )
        check_cancelled(cancel_event)  # type: ignore[arg-type]

        return transcribe_words(
            wav_path,
            model_size=model_size,
            language=whisper_lang,
            sample_rate=sample_rate,
            progress=progress,
            cancel_event=cancel_event,
        )
