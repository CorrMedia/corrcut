"""End-to-end AI detection pipeline (Phase 5)."""

from __future__ import annotations

import logging
import subprocess
import sys
import threading
from collections.abc import Callable
from pathlib import Path

from corrcut.ai.detection_result import DetectionResult
from corrcut.ai.match_events import same_detection_event
from corrcut.ai.run_detection_core import detect_from_media
from corrcut.ai.transcribe import (
    is_whisper_available,
    whisper_install_hint,
)
from corrcut.config import env_value
from corrcut.domain.ai_suggestion import AiSuggestion, DetectionSettings, SuggestionStatus
from corrcut.domain.project import Project
from corrcut.engine.hidden_process import no_window_kwargs
from corrcut.engine.jobs.cancel import JobCancelled

logger = logging.getLogger(__name__)


def run_detection(
    project: Project,
    settings: DetectionSettings,
    *,
    cache_dir: Path | None = None,
    cancel_event: threading.Event | None = None,
    log: Callable[[str], None] | None = None,
    progress: Callable[[int, str], None] | None = None,
    use_subprocess: bool | None = None,
) -> DetectionResult:
    """
    Transcribe selected dialogue track and propose profanity censor windows.

    Whisper transcription runs in an isolated subprocess by default. This keeps
    ``ctranslate2`` (faster-whisper's backend) out of the main process, which is
    required on Windows: PaddlePaddle (bitmap OCR) and ctranslate2 ship
    conflicting Intel OpenMP/MKL runtime DLLs, and loading ctranslate2 after
    PaddlePaddle crashes with ``OSError: [WinError 127]``. The subprocess uses
    ``CORRCUT_AI_PYTHON`` when set, otherwise the current interpreter.
    """
    if project.source is None:
        raise ValueError("project has no source media")
    track = project.selected_track()
    if track is None:
        raise ValueError("no audio track selected")

    if use_subprocess is None:
        # Isolate ctranslate2 from PaddlePaddle unless running frozen (where
        # ``sys.executable`` is the bundled app, not a Python that can run
        # ``-m corrcut.ai.worker``).
        use_subprocess = not getattr(sys, "frozen", False)

    if use_subprocess:
        return _run_detection_subprocess(
            project,
            settings,
            cache_dir=cache_dir,
            cancel_event=cancel_event,
            log=log,
            progress=progress,
        )

    if not is_whisper_available():
        raise RuntimeError(f"faster-whisper is not installed. {whisper_install_hint()}")

    def emit(line: str) -> None:
        if log is not None:
            log(line)
        logger.info(line)

    def report(percent: int, message: str) -> None:
        if progress is not None:
            progress(percent, message)

    source_path = project.source.path.resolve()
    probe = project.source.probe or {}
    duration = probe.get("duration_seconds")
    subtitle_streams = list(probe.get("subtitle_streams") or [])
    project_cues = [cue.to_json() for cue in project.subtitle_cues] if project.subtitle_cues else None

    return detect_from_media(
        source_path,
        stream_index=track.stream_index,
        dialogue_target_strategy=track.dialogue_target_strategy,
        channel_layout=track.channel_layout,
        sample_rate=track.sample_rate or 16000,
        language_raw=track.language,
        subtitle_streams=subtitle_streams,
        duration_seconds=float(duration) if duration is not None else None,
        settings=settings,
        cancel_event=cancel_event,
        progress=progress,
        log=emit,
        project_subtitle_cues=project_cues,
    )


def _run_detection_subprocess(
    project: Project,
    settings: DetectionSettings,
    *,
    cache_dir: Path | None,
    cancel_event: threading.Event | None,
    log: Callable[[str], None] | None,
    progress: Callable[[int, str], None] | None,
) -> DetectionResult:
    import json

    ai_python = env_value("AI_PYTHON") or sys.executable
    payload = {
        "source_path": str(project.source.path.resolve()) if project.source else "",
        "stream_index": project.selected_audio_stream_index,
        "track": _track_payload(project),
        "settings": settings.to_json(),
        "cache_dir": str(cache_dir) if cache_dir else None,
        "duration_seconds": project.source.probe.get("duration_seconds") if project.source else None,
        "subtitle_streams": list(
            (project.source.probe or {}).get("subtitle_streams") or []
        ),
        "project_subtitle_cues": (
            [cue.to_json() for cue in project.subtitle_cues] if project.subtitle_cues else None
        ),
    }

    logger.info("Running AI worker: %s -m corrcut.ai.worker", ai_python)

    proc = subprocess.Popen(
        [ai_python, "-m", "corrcut.ai.worker"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
        **no_window_kwargs(),
    )
    assert proc.stdin is not None
    proc.stdin.write(json.dumps(payload))
    proc.stdin.close()

    stderr_lines: list[str] = []
    assert proc.stdout is not None
    for line in proc.stdout:
        if cancel_event is not None and cancel_event.is_set():
            proc.kill()
            proc.wait(timeout=5)
            raise JobCancelled()
        stripped = line.rstrip()
        if stripped:
            stderr_lines.append(stripped)
            if log is not None:
                log(stripped)
            if stripped.startswith("PROGRESS "):
                try:
                    _, pct_s, msg = stripped.split(" ", 2)
                    if progress is not None:
                        progress(int(pct_s), msg)
                except ValueError:
                    pass

    code = proc.wait()
    if code != 0:
        if stderr_lines:
            logger.error("AI worker failed:\n%s", "\n".join(stderr_lines[-60:]))
            detail = stderr_lines[-1]
        else:
            detail = f"AI worker exit {code}"
        raise RuntimeError(detail)

    result_line = next((ln for ln in reversed(stderr_lines) if ln.startswith("RESULT ")), None)
    if result_line is None:
        raise RuntimeError("AI worker returned no RESULT line")

    data = json.loads(result_line[len("RESULT ") :])
    return DetectionResult(
        suggestions=[
            AiSuggestion.from_json(item) for item in data.get("suggestions", [])
        ],
        whisper_words=DetectionResult.whisper_words_from_json(
            list(data.get("whisper_words") or [])
        ),
    )


def _track_payload(project: Project) -> dict[str, object]:
    track = project.selected_track()
    if track is None:
        return {}
    return {
        "stream_index": track.stream_index,
        "sample_rate": track.sample_rate,
        "channel_layout": track.channel_layout,
        "dialogue_target_strategy": track.dialogue_target_strategy.value,
        "language": track.language,
    }


def suggestion_to_operation_channels(project: Project, stream_index: int) -> list[str]:
    from corrcut.domain.dialogue_target import default_operation_channels

    for track in project.tracks:
        if track.stream_index == stream_index:
            return default_operation_channels(track.dialogue_target_strategy)
    return []


def _operation_for_suggestion(op: object, suggestion: AiSuggestion) -> bool:
    from corrcut.domain.operation import Operation

    if not isinstance(op, Operation) or op.source != "ai":
        return False
    linked_id = op.params.get("suggestion_id")
    if linked_id is not None:
        return linked_id == suggestion.id
    return (
        op.scope.stream_index == suggestion.stream_index
        and op.time_range.start == suggestion.time_range.start
        and op.time_range.end == suggestion.time_range.end
    )


def apply_suggestion(project: Project, suggestion: AiSuggestion) -> None:
    """Convert an accepted suggestion into a mute Operation."""
    from corrcut.domain.corr_categories import categories_for_suggestion
    from corrcut.domain.operation import Operation, OperationType, default_params

    params = default_params(OperationType.MUTE)
    params["suggestion_id"] = suggestion.id
    language = None
    for track in project.tracks:
        if track.stream_index == suggestion.stream_index:
            language = project.track_language(track)[0]
            break
    op = Operation.create(
        OperationType.MUTE,
        suggestion.stream_index,
        suggestion_to_operation_channels(project, suggestion.stream_index),
        suggestion.time_range.start,
        suggestion.time_range.end,
        order=len(project.operations),
        params=params,
        source="ai",
        language=language,
        categories=categories_for_suggestion(
            matched_term=suggestion.matched_term,
            matched_word=suggestion.matched_word,
            category=suggestion.category,
        ),
    )
    project.operations.append(op)
    suggestion.status = SuggestionStatus.ACCEPTED


def revoke_suggestion(project: Project, suggestion: AiSuggestion) -> None:
    """Reject an accepted suggestion and remove its linked mute operation."""
    _remove_suggestion_operation(project, suggestion)
    suggestion.status = SuggestionStatus.REJECTED


def reset_suggestion_to_pending(project: Project, suggestion: AiSuggestion) -> None:
    """Return a reviewed suggestion to pending, removing any linked mute operation."""
    _remove_suggestion_operation(project, suggestion)
    suggestion.status = SuggestionStatus.PENDING


def _remove_suggestion_operation(project: Project, suggestion: AiSuggestion) -> None:
    project.operations = [
        op for op in project.operations if not _operation_for_suggestion(op, suggestion)
    ]


def _find_matching_hit(
    candidates: list[AiSuggestion],
    new: AiSuggestion,
    *,
    slop_ms: int = 750,
) -> AiSuggestion | None:
    for candidate in candidates:
        if same_detection_event(candidate, new, slop_ms=slop_ms):
            return candidate
    return None


def _suggestion_detection_changed(existing: AiSuggestion, new: AiSuggestion) -> bool:
    return (
        existing.time_range.start.seconds != new.time_range.start.seconds
        or existing.time_range.end.seconds != new.time_range.end.seconds
        or existing.confidence != new.confidence
        or existing.whisper_probability != new.whisper_probability
        or existing.matched_word != new.matched_word
        or existing.transcript_phrase != new.transcript_phrase
        or existing.alignment_quality != new.alignment_quality
        or existing.category != new.category
        or existing.detection_source != new.detection_source
    )


def _apply_detection_fields(existing: AiSuggestion, new: AiSuggestion) -> None:
    existing.time_range = new.time_range
    existing.matched_word = new.matched_word
    existing.matched_term = new.matched_term
    existing.transcript_phrase = new.transcript_phrase
    existing.confidence = new.confidence
    existing.whisper_probability = new.whisper_probability
    existing.alignment_quality = new.alignment_quality
    existing.category = new.category
    existing.detection_source = new.detection_source


def merge_suggestions_for_track(
    project: Project,
    new_suggestions: list[AiSuggestion],
    stream_index: int,
    *,
    merge_slop_ms: int = 750,
) -> None:
    """Merge new detections into the track; keep history and dedupe pending hits."""
    history = [
        s
        for s in project.ai_suggestions
        if s.stream_index != stream_index or s.status != SuggestionStatus.PENDING
    ]
    existing_pending = [
        s
        for s in project.ai_suggestions
        if s.stream_index == stream_index and s.status == SuggestionStatus.PENDING
    ]
    unused_pending = list(existing_pending)
    merged_pending: list[AiSuggestion] = []

    for new in new_suggestions:
        prior = _find_matching_hit(history, new, slop_ms=merge_slop_ms)
        if prior is not None and not _suggestion_detection_changed(prior, new):
            continue

        pending_match = _find_matching_hit(unused_pending, new, slop_ms=merge_slop_ms)
        if pending_match is not None:
            if _suggestion_detection_changed(pending_match, new):
                _apply_detection_fields(pending_match, new)
            merged_pending.append(pending_match)
            unused_pending.remove(pending_match)
            continue

        merged_pending.append(new)

    project.ai_suggestions = history + merged_pending
