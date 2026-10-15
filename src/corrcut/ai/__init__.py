"""AI transcription and profanity detection (Phase 5)."""

from corrcut.ai.detect import detect_suggestions
from corrcut.ai.lexicon import DEFAULT_LEXICON, ProfanityLexicon
from corrcut.ai.pipeline import (
    apply_suggestion,
    merge_suggestions_for_track,
    run_detection,
    suggestion_to_operation_channels,
)
from corrcut.ai.transcribe import is_whisper_available, whisper_install_hint

__all__ = [
    "DEFAULT_LEXICON",
    "ProfanityLexicon",
    "apply_suggestion",
    "detect_suggestions",
    "is_whisper_available",
    "merge_suggestions_for_track",
    "run_detection",
    "suggestion_to_operation_channels",
    "whisper_install_hint",
]
