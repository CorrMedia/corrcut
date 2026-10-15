"""Shared in-process detection: Whisper + optional subtitles, cross-source merge."""

from __future__ import annotations

import logging
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import Any

from corrcut.ai.detect import detect_suggestions
from corrcut.ai.detection_result import DetectionResult
from corrcut.ai.extract import extract_transcription_wav
from corrcut.ai.language import whisper_language_code
from corrcut.ai.merge_cross_source import merge_cross_source
from corrcut.ai.subtitles import load_subtitle_words
from corrcut.ai.transcribe import transcribe_words
from corrcut.domain.ai_suggestion import AiSuggestion, DetectionSettings
from corrcut.domain.dialogue_target import DialogueTargetStrategy
from corrcut.engine.jobs.cancel import check_cancelled

logger = logging.getLogger(__name__)


def detect_from_media(
    source_path: Path,
    *,
    stream_index: int,
    dialogue_target_strategy: DialogueTargetStrategy,
    channel_layout: str | None,
    sample_rate: int,
    language_raw: str | None,
    subtitle_streams: list[dict[str, Any]],
    duration_seconds: float | None,
    settings: DetectionSettings,
    cancel_event: object | None = None,
    progress: Callable[[int, str], None] | None = None,
    log: Callable[[str], None] | None = None,
    project_subtitle_cues: list[dict[str, Any]] | None = None,
) -> DetectionResult:
    def emit(line: str) -> None:
        if log is not None:
            log(line)
        logger.info(line)

    def report(percent: int, message: str) -> None:
        if progress is not None:
            progress(percent, message)

    language = whisper_language_code(settings.language or language_raw)
    duration = float(duration_seconds) if duration_seconds is not None else None

    report(0, "Extracting dialogue audio…")
    with tempfile.TemporaryDirectory(prefix="ms_ai_") as tmp:
        wav_path = Path(tmp) / "dialogue.wav"
        extract_transcription_wav(
            source_path,
            stream_index,
            wav_path,
            strategy=dialogue_target_strategy,
            channel_layout=channel_layout,
            cancel_event=cancel_event,
        )
        check_cancelled(cancel_event)  # type: ignore[arg-type]

        words = transcribe_words(
            wav_path,
            model_size=settings.whisper_model,
            language=language,
            sample_rate=sample_rate,
            progress=progress,
            cancel_event=cancel_event,
        )
        check_cancelled(cancel_event)  # type: ignore[arg-type]

    report(88, "Matching profanity lexicon (audio)…")
    whisper_suggestions = detect_suggestions(
        words,
        stream_index=stream_index,
        settings=settings,
        track_language=language,
        duration_seconds=duration,
        timing_source="audio",
    )

    subtitle_suggestions: list[AiSuggestion] = []
    if settings.use_subtitles and (subtitle_streams or project_subtitle_cues):
        report(90, "Loading subtitles…")
        sub_words, sub_index = load_subtitle_words(
            source_path,
            subtitle_streams,
            preferred_stream_index=settings.subtitle_stream_index,
            audio_language=language_raw,
            sample_rate=sample_rate,
            log=logger,
            project_cues=project_subtitle_cues,
        )
        if sub_words:
            label = f"subtitles #{sub_index}" if sub_index is not None else "project subtitles"
            emit(f"Matching profanity lexicon ({label})…")
            subtitle_suggestions = detect_suggestions(
                sub_words,
                stream_index=stream_index,
                settings=settings,
                track_language=language,
                duration_seconds=duration,
                timing_source="subtitle",
            )

    if subtitle_suggestions:
        suggestions = merge_cross_source(
            whisper_suggestions,
            subtitle_suggestions,
            slop_ms=settings.cross_source_merge_slop_ms,
        )
    else:
        suggestions = whisper_suggestions

    emit(f"Found {len(suggestions)} suggestion(s)")
    report(100, "Detection complete")
    return DetectionResult(suggestions=suggestions, whisper_words=tuple(words))
