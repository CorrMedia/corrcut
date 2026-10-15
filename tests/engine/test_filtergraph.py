from fractions import Fraction

from corrcut.domain.dialogue_target import DialogueTargetStrategy
from corrcut.domain.operation import Operation, OperationType
from corrcut.domain.project import TrackRecord
from corrcut.domain.time_point import TimePoint
from corrcut.engine.filtergraph import CompileContext, FiltergraphCompiler, layout_channel_labels


def _track(layout: str, strategy: DialogueTargetStrategy) -> TrackRecord:
    return TrackRecord(
        stream_index=1,
        codec="aac",
        channel_layout=layout,
        channels=6 if layout == "5.1" else 2,
        sample_rate=48000,
        language="eng",
        dialogue_target_strategy=strategy,
    )


def test_beep_compiles_mute_plus_sine_overlay() -> None:
    track = _track("stereo", DialogueTargetStrategy.FULL_MIX)
    op = Operation.create(
        OperationType.BEEP,
        1,
        [],
        TimePoint(Fraction(1)),
        TimePoint(Fraction(2)),
    )
    compiled = FiltergraphCompiler().compile([op], CompileContext(track=track))
    assert "volume=0.0:enable='between(t,1.0,2.0)" in compiled.filter_complex
    assert (
        "aeval=exprs='val(0)+0.3*sin(2*PI*1000.0*t)*between(t,1.0,2.0)'" in compiled.filter_complex
    )


def test_mute_compiles_hard_gate() -> None:
    track = _track("stereo", DialogueTargetStrategy.FULL_MIX)
    op = Operation.create(
        OperationType.MUTE,
        1,
        [],
        TimePoint(Fraction(1)),
        TimePoint(Fraction(2)),
    )
    compiled = FiltergraphCompiler().compile([op], CompileContext(track=track))
    assert "volume=0.0:enable='between(t,1.0,2.0)" in compiled.filter_complex
    assert "volume=0.0:enable" in compiled.filter_complex


def test_center_channel_uses_split() -> None:
    track = _track("5.1", DialogueTargetStrategy.CENTER_CHANNEL)
    op = Operation.create(
        OperationType.MUTE, 1, ["FC"], TimePoint(Fraction(0)), TimePoint(Fraction(1))
    )
    compiled = FiltergraphCompiler().compile([op], CompileContext(track=track))
    assert "asplit=6" in compiled.filter_complex
    assert "pan=mono|c0=FC[fc_src]" in compiled.filter_complex
    assert "join=inputs=6" in compiled.filter_complex
    assert "pan=mono|c0=FC[fc_src]" in compiled.filter_complex
    assert "[fc_src]" in compiled.filter_complex
    assert "volume=0.0:enable='between(t,0.0,1.0)" in compiled.filter_complex


def test_center_channel_71_joins_eight_inputs() -> None:
    track = TrackRecord(
        stream_index=1,
        codec="aac",
        channel_layout="7.1",
        channels=8,
        sample_rate=48000,
        language="eng",
        dialogue_target_strategy=DialogueTargetStrategy.CENTER_CHANNEL,
    )
    op = Operation.create(
        OperationType.MUTE, 1, ["FC"], TimePoint(Fraction(0)), TimePoint(Fraction(1))
    )
    compiled = FiltergraphCompiler().compile([op], CompileContext(track=track))
    assert "join=inputs=8" in compiled.filter_complex
    assert "[ms_SL]" in compiled.filter_complex


def test_center_channel_join_uses_explicit_channel_map() -> None:
    track = _track("5.1", DialogueTargetStrategy.CENTER_CHANNEL)
    op = Operation.create(
        OperationType.MUTE, 1, ["FC"], TimePoint(Fraction(0)), TimePoint(Fraction(1))
    )
    compiled = FiltergraphCompiler().compile([op], CompileContext(track=track))
    assert (
        "join=inputs=6:channel_layout=5.1:map=0.0-FL|1.0-FR|2.0-FC|3.0-LFE|4.0-BL|5.0-BR"
        in compiled.filter_complex
    )


def test_center_channel_51_side_extracts_side_channels() -> None:
    track = TrackRecord(
        stream_index=1,
        codec="ac3",
        channel_layout="5.1(side)",
        channels=6,
        sample_rate=48000,
        language="eng",
        dialogue_target_strategy=DialogueTargetStrategy.CENTER_CHANNEL,
    )
    op = Operation.create(
        OperationType.MUTE, 1, ["FC"], TimePoint(Fraction(0)), TimePoint(Fraction(1))
    )
    compiled = FiltergraphCompiler().compile([op], CompileContext(track=track))
    # Source has SL/SR (not BL/BR); extracting BL/BR would silence the surrounds.
    assert "pan=mono|c0=SL[ms_SL]" in compiled.filter_complex
    assert "pan=mono|c0=SR[ms_SR]" in compiled.filter_complex
    assert "c0=BL" not in compiled.filter_complex
    assert (
        "channel_layout=5.1(side):map=0.0-FL|1.0-FR|2.0-FC|3.0-LFE|4.0-SL|5.0-SR"
        in compiled.filter_complex
    )


def test_center_channel_71_wide_extracts_front_corner_channels() -> None:
    track = TrackRecord(
        stream_index=1,
        codec="ac3",
        channel_layout="7.1(wide)",
        channels=8,
        sample_rate=48000,
        language="eng",
        dialogue_target_strategy=DialogueTargetStrategy.CENTER_CHANNEL,
    )
    op = Operation.create(
        OperationType.MUTE, 1, ["FC"], TimePoint(Fraction(0)), TimePoint(Fraction(1))
    )
    compiled = FiltergraphCompiler().compile([op], CompileContext(track=track))
    # 7.1(wide) source has FLC/FRC (not SL/SR).
    assert "pan=mono|c0=FLC[ms_FLC]" in compiled.filter_complex
    assert "pan=mono|c0=FRC[ms_FRC]" in compiled.filter_complex
    assert "c0=SL" not in compiled.filter_complex
    assert "6.0-FLC|7.0-FRC" in compiled.filter_complex


def test_layout_channel_labels_match_channel_count() -> None:
    assert layout_channel_labels("7.1", 8) is not None
    assert layout_channel_labels("7.1", 6) is None


def test_compile_for_audio_stream_preserves_filter_time_origin() -> None:
    track = _track("5.1", DialogueTargetStrategy.CENTER_CHANNEL)
    op = Operation.create(
        OperationType.MUTE,
        1,
        ["FC"],
        TimePoint(Fraction(100)),
        TimePoint(Fraction(101)),
    )
    compiled = FiltergraphCompiler().compile_for_audio_stream(
        [op],
        CompileContext(track=track, filter_time_origin_s=98.5),
        audio_ordinal=0,
    )
    assert "volume=0.0:enable='between(t,1.5,2.5)" in compiled.filter_complex


def test_filter_time_origin_clamps_mute_that_started_before_segment() -> None:
    track = _track("5.1", DialogueTargetStrategy.CENTER_CHANNEL)
    op = Operation.create(
        OperationType.MUTE,
        1,
        ["FC"],
        TimePoint(Fraction(28928375, 10000)),
        TimePoint(Fraction(28934375, 10000)),
    )
    compiled = FiltergraphCompiler().compile_for_audio_stream(
        [op],
        CompileContext(track=track, filter_time_origin_s=2892.9375),
        audio_ordinal=0,
    )
    assert "st=-" not in compiled.filter_complex
    assert "volume=0.0:enable='between(t,0.0,0.5)" in compiled.filter_complex
    assert "pan=mono|c0=FC[fc_src]" in compiled.filter_complex


def test_audition_preview_fl_fr_fc_pan_syntax() -> None:
    track = _track("5.1", DialogueTargetStrategy.CENTER_CHANNEL)
    op = Operation.create(
        OperationType.MUTE,
        1,
        ["FC", "FL", "FR"],
        TimePoint(Fraction(1)),
        TimePoint(Fraction(2)),
    )
    compiled = FiltergraphCompiler().compile(
        [op],
        CompileContext(
            track=track,
            filter_time_origin_s=0.5,
            audition_preview_channels=frozenset({"FC", "FL", "FR"}),
        ),
    )
    assert "FR=0*c0" in compiled.filter_complex
    assert "FL=0*c0" in compiled.filter_complex
    assert "amix=inputs=3" in compiled.filter_complex
    assert "|FR=0]" not in compiled.filter_complex


def test_audition_preview_fc_only_skips_join() -> None:
    track = _track("5.1", DialogueTargetStrategy.CENTER_CHANNEL)
    op = Operation.create(
        OperationType.MUTE,
        1,
        ["FC"],
        TimePoint(Fraction(1)),
        TimePoint(Fraction(2)),
    )
    compiled = FiltergraphCompiler().compile(
        [op],
        CompileContext(
            track=track,
            filter_time_origin_s=1.0,
            audition_preview_channels=frozenset({"FC"}),
        ),
    )
    assert "join=inputs=" not in compiled.filter_complex
    assert "atrim=" not in compiled.filter_complex
    assert "volume=0.0:enable='between(t,0.0,1.0)" in compiled.filter_complex
    assert "pan=stereo|FL=c0|FR=c0" in compiled.filter_complex


def test_filter_time_origin_relativizes_mute_for_audition() -> None:
    track = _track("5.1", DialogueTargetStrategy.CENTER_CHANNEL)
    op = Operation.create(
        OperationType.MUTE,
        1,
        ["FC"],
        TimePoint(Fraction(100)),
        TimePoint(Fraction(101)),
    )
    compiled = FiltergraphCompiler().compile(
        [op],
        CompileContext(track=track, filter_time_origin_s=98.5),
    )
    assert "volume=0.0:enable='between(t,1.5,2.5)" in compiled.filter_complex


def test_center_channel_applies_mute_to_fl_fr_when_scoped() -> None:
    track = _track("5.1", DialogueTargetStrategy.CENTER_CHANNEL)
    op = Operation.create(
        OperationType.MUTE,
        1,
        ["FC", "FL", "FR"],
        TimePoint(Fraction(0)),
        TimePoint(Fraction(1)),
    )
    compiled = FiltergraphCompiler().compile([op], CompileContext(track=track))
    assert "[fc_src]" in compiled.filter_complex or "[fc_e]" in compiled.filter_complex
    assert "[ms_FL]" in compiled.filter_complex
    assert "[ms_FR]" in compiled.filter_complex
    assert "ms_FC2" not in compiled.filter_complex
