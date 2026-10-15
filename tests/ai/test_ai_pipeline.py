"""Apply AI suggestions to operations."""

from fractions import Fraction

from corrcut.ai.match_events import same_detection_event
from corrcut.ai.pipeline import (
    apply_suggestion,
    merge_suggestions_for_track,
    reset_suggestion_to_pending,
    revoke_suggestion,
)
from corrcut.domain.ai_suggestion import AiSuggestion, SuggestionStatus
from corrcut.domain.dialogue_target import DialogueTargetStrategy
from corrcut.domain.operation import OperationType, TimeRange
from corrcut.domain.project import Project, SourceFileRecord, TrackRecord
from corrcut.domain.time_point import TimePoint


def _project() -> Project:
    track = TrackRecord(
        stream_index=1,
        codec="aac",
        channel_layout="stereo",
        channels=2,
        sample_rate=48000,
        language="eng",
        dialogue_target_strategy=DialogueTargetStrategy.FULL_MIX,
    )
    return Project(
        source=SourceFileRecord(path=__import__("pathlib").Path("m.mkv"), fingerprint={}, probe={}),
        tracks=[track],
        selected_audio_stream_index=1,
    )


def test_apply_suggestion_creates_mute_operation() -> None:
    project = _project()
    suggestion = AiSuggestion.create(
        stream_index=1,
        time_range=TimeRange(TimePoint(Fraction(1)), TimePoint(Fraction(2))),
        matched_word="fuck",
        matched_term="fuck",
        transcript_phrase="what the fuck",
        confidence=0.9,
        whisper_probability=0.92,
        alignment_quality="good",
    )
    apply_suggestion(project, suggestion)
    assert len(project.operations) == 1
    op = project.operations[0]
    assert op.type == OperationType.MUTE
    assert op.source == "ai"
    assert op.params["suggestion_id"] == suggestion.id
    assert op.categories == ["word_fuck"]
    assert suggestion.status == SuggestionStatus.ACCEPTED


def test_revoke_suggestion_removes_linked_mute_operation() -> None:
    project = _project()
    suggestion = AiSuggestion.create(
        stream_index=1,
        time_range=TimeRange(TimePoint(Fraction(1)), TimePoint(Fraction(2))),
        matched_word="fuck",
        matched_term="fuck",
        transcript_phrase="what the fuck",
        confidence=0.9,
        whisper_probability=0.92,
        alignment_quality="good",
    )
    apply_suggestion(project, suggestion)
    revoke_suggestion(project, suggestion)
    assert project.operations == []
    assert suggestion.status == SuggestionStatus.REJECTED


def test_reset_suggestion_to_pending_removes_mute_and_clears_status() -> None:
    project = _project()
    suggestion = AiSuggestion.create(
        stream_index=1,
        time_range=TimeRange(TimePoint(Fraction(1)), TimePoint(Fraction(2))),
        matched_word="fuck",
        matched_term="fuck",
        transcript_phrase="what the fuck",
        confidence=0.9,
        whisper_probability=0.92,
        alignment_quality="good",
    )
    apply_suggestion(project, suggestion)
    reset_suggestion_to_pending(project, suggestion)
    assert project.operations == []
    assert suggestion.status == SuggestionStatus.PENDING


def test_merge_replaces_pending_only() -> None:
    project = _project()
    old = AiSuggestion.create(
        stream_index=1,
        time_range=TimeRange(TimePoint(Fraction(1)), TimePoint(Fraction(2))),
        matched_word="a",
        matched_term="ass",
        transcript_phrase="a",
        confidence=0.5,
        whisper_probability=0.5,
        alignment_quality="good",
    )
    accepted = AiSuggestion.create(
        stream_index=1,
        time_range=TimeRange(TimePoint(Fraction(3)), TimePoint(Fraction(4))),
        matched_word="b",
        matched_term="bitch",
        transcript_phrase="b",
        confidence=0.9,
        whisper_probability=0.9,
        alignment_quality="good",
    )
    accepted.status = SuggestionStatus.ACCEPTED
    project.ai_suggestions = [old, accepted]

    new = AiSuggestion.create(
        stream_index=1,
        time_range=TimeRange(TimePoint(Fraction(5)), TimePoint(Fraction(6))),
        matched_word="c",
        matched_term="crap",
        transcript_phrase="c",
        confidence=0.7,
        whisper_probability=0.7,
        alignment_quality="good",
    )
    merge_suggestions_for_track(project, [new], 1)
    assert len(project.ai_suggestions) == 2
    assert project.ai_suggestions[0].status == SuggestionStatus.ACCEPTED
    assert project.ai_suggestions[1].matched_term == "crap"


def test_merge_keeps_pending_id_when_redetected() -> None:
    project = _project()
    pending = AiSuggestion.create(
        stream_index=1,
        time_range=TimeRange(TimePoint(Fraction(1)), TimePoint(Fraction(2))),
        matched_word="fuck",
        matched_term="fuck",
        transcript_phrase="what the fuck",
        confidence=0.9,
        whisper_probability=0.92,
        alignment_quality="good",
    )
    project.ai_suggestions = [pending]

    redetected = AiSuggestion.create(
        stream_index=1,
        time_range=TimeRange(TimePoint(Fraction(1, 10)), TimePoint(Fraction(21, 10))),
        matched_word="fuck",
        matched_term="fuck",
        transcript_phrase="what the fuck",
        confidence=0.9,
        whisper_probability=0.92,
        alignment_quality="good",
    )
    merge_suggestions_for_track(project, [redetected], 1)
    assert len(project.ai_suggestions) == 1
    assert project.ai_suggestions[0].id == pending.id


def test_merge_updates_pending_when_detection_changes() -> None:
    project = _project()
    pending = AiSuggestion.create(
        stream_index=1,
        time_range=TimeRange(TimePoint(Fraction(1)), TimePoint(Fraction(2))),
        matched_word="fuck",
        matched_term="fuck",
        transcript_phrase="what the fuck",
        confidence=0.6,
        whisper_probability=0.6,
        alignment_quality="low",
    )
    project.ai_suggestions = [pending]

    updated = AiSuggestion.create(
        stream_index=1,
        time_range=TimeRange(TimePoint(Fraction(1)), TimePoint(Fraction(2))),
        matched_word="fuck",
        matched_term="fuck",
        transcript_phrase="what the fuck",
        confidence=0.95,
        whisper_probability=0.95,
        alignment_quality="good",
    )
    merge_suggestions_for_track(project, [updated], 1)
    assert len(project.ai_suggestions) == 1
    assert project.ai_suggestions[0].id == pending.id
    assert project.ai_suggestions[0].confidence == 0.95
    assert project.ai_suggestions[0].alignment_quality == "good"


def test_merge_pending_uses_center_slop() -> None:
    project = _project()
    pending = AiSuggestion.create(
        stream_index=1,
        time_range=TimeRange(TimePoint(Fraction(10)), TimePoint(Fraction(11))),
        matched_word="fuck",
        matched_term="fuck",
        transcript_phrase="what the fuck",
        confidence=0.9,
        whisper_probability=0.92,
        alignment_quality="good",
    )
    project.ai_suggestions = [pending]

    offset = AiSuggestion.create(
        stream_index=1,
        time_range=TimeRange(TimePoint(Fraction(101, 10)), TimePoint(Fraction(111, 10))),
        matched_word="fuck",
        matched_term="fuck",
        transcript_phrase="what the fuck",
        confidence=0.9,
        whisper_probability=0.92,
        alignment_quality="good",
    )
    assert same_detection_event(pending, offset, slop_ms=750)
    merge_suggestions_for_track(project, [offset], 1, merge_slop_ms=750)
    assert len(project.ai_suggestions) == 1
    assert project.ai_suggestions[0].id == pending.id


def test_merge_skips_rejected_hit_unless_changed() -> None:
    project = _project()
    rejected = AiSuggestion.create(
        stream_index=1,
        time_range=TimeRange(TimePoint(Fraction(1)), TimePoint(Fraction(2))),
        matched_word="fuck",
        matched_term="fuck",
        transcript_phrase="what the fuck",
        confidence=0.9,
        whisper_probability=0.92,
        alignment_quality="good",
    )
    rejected.status = SuggestionStatus.REJECTED
    project.ai_suggestions = [rejected]

    same = AiSuggestion.create(
        stream_index=1,
        time_range=TimeRange(TimePoint(Fraction(1)), TimePoint(Fraction(2))),
        matched_word="fuck",
        matched_term="fuck",
        transcript_phrase="what the fuck",
        confidence=0.9,
        whisper_probability=0.92,
        alignment_quality="good",
    )
    merge_suggestions_for_track(project, [same], 1)
    assert len(project.ai_suggestions) == 1
    assert project.ai_suggestions[0].status == SuggestionStatus.REJECTED
