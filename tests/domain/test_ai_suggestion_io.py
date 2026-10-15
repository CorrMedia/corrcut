"""AI suggestion JSON persistence."""

from fractions import Fraction

from corrcut.domain.ai_suggestion import AiSuggestion, SuggestionStatus
from corrcut.domain.dialogue_target import DialogueTargetStrategy
from corrcut.domain.operation import TimeRange
from corrcut.domain.project import Project, SourceFileRecord, TrackRecord
from corrcut.domain.time_point import TimePoint


def test_ai_suggestion_json_roundtrip() -> None:
    suggestion = AiSuggestion.create(
        stream_index=1,
        time_range=TimeRange(
            start=TimePoint(Fraction(1)),
            end=TimePoint(Fraction(2)),
        ),
        matched_word="shit",
        matched_term="shit",
        transcript_phrase="oh shit",
        confidence=0.88,
        whisper_probability=0.91,
        alignment_quality="good",
    )
    restored = AiSuggestion.from_json(suggestion.to_json())
    assert restored.matched_word == "shit"
    assert restored.confidence == 0.88


def test_project_persists_ai_suggestions() -> None:
    track = TrackRecord(
        stream_index=0,
        codec="aac",
        channel_layout="stereo",
        channels=2,
        sample_rate=48000,
        language="eng",
        dialogue_target_strategy=DialogueTargetStrategy.FULL_MIX,
    )
    suggestion = AiSuggestion.create(
        stream_index=0,
        time_range=TimeRange(TimePoint(Fraction(1)), TimePoint(Fraction(2))),
        matched_word="damn",
        matched_term="damn",
        transcript_phrase="damn it",
        confidence=0.8,
        whisper_probability=0.85,
        alignment_quality="good",
    )
    project = Project(
        source=SourceFileRecord(path=__import__("pathlib").Path("a.mkv"), fingerprint={}, probe={}),
        tracks=[track],
        ai_suggestions=[suggestion],
        selected_audio_stream_index=0,
    )
    loaded = Project.from_json(project.to_json())
    assert len(loaded.ai_suggestions) == 1
    assert loaded.ai_suggestions[0].status == SuggestionStatus.PENDING
