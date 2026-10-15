"""Audio edits follow language, with channels adjusted per layout."""

from fractions import Fraction

from corrcut.domain.audio_language import edit_language_label, resolve_track_language
from corrcut.domain.channel_layout import channels_for_layout
from corrcut.domain.corr_sidecar import (
    operations_from_corr_document,
    operations_to_corr_document,
)
from corrcut.domain.dialogue_target import DialogueTargetStrategy
from corrcut.domain.operation import Operation, OperationType
from corrcut.domain.project import Project, TrackRecord
from corrcut.domain.time_point import TimePoint
from corrcut.engine.filtergraph import CompileContext, FiltergraphCompiler


def _track(
    index: int,
    layout: str,
    channels: int,
    language: str | None,
    *,
    title: str | None = None,
    strategy: DialogueTargetStrategy | None = None,
) -> TrackRecord:
    if strategy is None:
        strategy = (
            DialogueTargetStrategy.CENTER_CHANNEL
            if channels >= 6
            else DialogueTargetStrategy.FULL_MIX
        )
    return TrackRecord(
        stream_index=index,
        codec="aac",
        channel_layout=layout,
        channels=channels,
        sample_rate=48000,
        language=language,
        dialogue_target_strategy=strategy,
        title=title,
    )


def test_missing_language_defaults_to_english() -> None:
    code, assumed = resolve_track_language(None, "Commentary")
    assert code == "eng"
    assert assumed
    code, assumed = resolve_track_language("und", None)
    assert code == "eng"
    assert assumed
    code, assumed = resolve_track_language(None, "English Stereo")
    assert code == "eng"
    assert not assumed
    code, assumed = resolve_track_language("en-US")
    assert code == "eng"
    assert not assumed


def test_edit_language_label_marks_only_the_english_fallback() -> None:
    assert edit_language_label(None, track_language=("eng", True)) == "English (assumed)"
    assert edit_language_label("eng", track_language=("eng", True)) == "English (assumed)"
    assert edit_language_label("eng", track_language=("eng", False)) == "English"
    assert edit_language_label("spa", track_language=("eng", True)) == "Spanish"
    assert edit_language_label("spa", track_language=None) == "Spanish"
    assert edit_language_label(None, track_language=None) == "English (assumed)"


def test_english_center_mute_applies_to_5_1_and_stereo() -> None:
    surround = _track(1, "7.1", 8, "eng")
    side = _track(2, "5.1", 6, "eng")
    stereo = _track(3, "stereo", 2, "eng")
    spanish = _track(4, "stereo", 2, "spa")
    mute = Operation.create(
        OperationType.MUTE,
        1,
        ["FC"],
        TimePoint(Fraction(1)),
        TimePoint(Fraction(2)),
        language="eng",
    )
    project = Project(
        tracks=[surround, side, stereo, spanish],
        operations=[mute],
        selected_audio_stream_index=1,
    )

    project.selected_audio_stream_index = 2
    five = project.operations_for_selected_track()
    assert len(five) == 1
    assert five[0].scope.channels == ["FC"]
    graph = FiltergraphCompiler().compile(five, CompileContext(track=side))
    assert "fc_e" in graph.filter_complex

    project.selected_audio_stream_index = 3
    folded = project.operations_for_selected_track()
    assert len(folded) == 1
    assert folded[0].scope.channels == []
    stereo_graph = FiltergraphCompiler().compile(folded, CompileContext(track=stereo))
    assert "volume=" in stereo_graph.filter_complex

    project.selected_audio_stream_index = 4
    assert project.operations_for_selected_track() == []
    assert project.timeline_operations() == []


def test_side_surround_mute_does_not_silence_stereo() -> None:
    assert channels_for_layout(["SL", "SR"], "5.1", 6) == ["BL", "BR"]
    assert channels_for_layout(["SL", "SR"], "stereo", 2) is None
    assert channels_for_layout(["FC"], "stereo", 2) == []


def test_language_correction_retags_edits_on_that_stream_only() -> None:
    eng = _track(1, "7.1", 8, "eng")
    untitled = _track(2, "stereo", 2, None, title="Commentary")
    on_surround = Operation.create(
        OperationType.MUTE,
        1,
        ["FC"],
        TimePoint(Fraction(1)),
        TimePoint(Fraction(2)),
    )
    on_stereo = Operation.create(
        OperationType.MUTE,
        2,
        [],
        TimePoint(Fraction(3)),
        TimePoint(Fraction(4)),
    )
    project = Project(
        tracks=[eng, untitled],
        operations=[on_surround, on_stereo],
        selected_audio_stream_index=2,
    )
    assert {op.id for op in project.timeline_operations()} == {on_surround.id, on_stereo.id}
    project.set_track_language(2, "spa")
    assert on_stereo.language == "spa"
    assert on_surround.language is None
    assert [op.id for op in project.timeline_operations()] == [on_stereo.id]
    project.selected_audio_stream_index = 1
    assert [op.id for op in project.timeline_operations()] == [on_surround.id]


def test_corr_round_trip_writes_language_and_matches_track() -> None:
    eng = _track(1, "7.1", 8, "eng")
    spa = _track(2, "stereo", 2, "spa")
    mute = Operation.create(
        OperationType.MUTE,
        1,
        ["FC"],
        TimePoint(Fraction(1)),
        TimePoint(Fraction(2)),
        language="eng",
    )
    project = Project(
        tracks=[eng, spa],
        operations=[mute],
        selected_audio_stream_index=2,
    )
    document = operations_to_corr_document(project)
    assert document["edits"][0]["language"] == "eng"
    loaded = Project(tracks=[eng, spa], selected_audio_stream_index=2)
    loaded.operations = operations_from_corr_document(
        document,
        default_stream_index=2,
        tracks=loaded.tracks,
    )
    loaded.select_track_for_audio_edits()
    assert loaded.selected_audio_stream_index == 1
    assert loaded.operations[0].language == "eng"
    assert loaded.operations[0].scope.stream_index == 1
    assert len(loaded.timeline_operations()) == 1
