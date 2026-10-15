"""Preview audio filtergraph — hard edges (no corr.json fades)."""

from fractions import Fraction

from corrcut.domain.dialogue_target import DialogueTargetStrategy
from corrcut.domain.operation import Operation, OperationType
from corrcut.domain.project import TrackRecord
from corrcut.domain.time_point import TimePoint
from corrcut.engine.filtergraph import CompileContext, FiltergraphCompiler


def _stereo_track() -> TrackRecord:
    return TrackRecord(
        stream_index=0,
        codec="aac",
        channel_layout="stereo",
        channels=2,
        sample_rate=48000,
        language=None,
        dialogue_target_strategy=DialogueTargetStrategy.FULL_MIX,
    )


def test_volume_compiles_hard_gain() -> None:
    op = Operation.create(
        OperationType.VOLUME,
        0,
        [],
        TimePoint(Fraction(1)),
        TimePoint(Fraction(2)),
    )
    compiled = FiltergraphCompiler().compile([op], CompileContext(track=_stereo_track()))
    assert "volume=0.2:enable='between(t,1.0,2.0)'" in compiled.filter_complex


def test_mute_hard_gate() -> None:
    op = Operation.create(
        OperationType.MUTE,
        0,
        [],
        TimePoint(Fraction(0)),
        TimePoint(Fraction(1)),
    )
    compiled = FiltergraphCompiler().compile([op], CompileContext(track=_stereo_track()))
    assert "afade=" not in compiled.filter_complex
    assert "volume=0.0:enable='between(t,0.0,1.0)'" in compiled.filter_complex


def test_beep_includes_sine() -> None:
    op = Operation.create(
        OperationType.BEEP,
        0,
        [],
        TimePoint(Fraction(1)),
        TimePoint(Fraction(2)),
    )
    compiled = FiltergraphCompiler().compile([op], CompileContext(track=_stereo_track()))
    assert "aeval=" in compiled.filter_complex
    assert "1000" in compiled.filter_complex
