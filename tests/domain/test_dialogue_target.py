from corrcut.domain.dialogue_target import (
    DialogueTargetStrategy,
    default_operation_channels,
    strategy_for_layout,
)


def test_surround_gets_center() -> None:
    assert strategy_for_layout("5.1") == DialogueTargetStrategy.CENTER_CHANNEL
    assert strategy_for_layout("7.1") == DialogueTargetStrategy.CENTER_CHANNEL
    assert strategy_for_layout("7.1(wide-side)") == DialogueTargetStrategy.CENTER_CHANNEL


def test_stereo_gets_full_mix() -> None:
    assert strategy_for_layout("stereo") == DialogueTargetStrategy.FULL_MIX


def test_unknown_gets_full_mix() -> None:
    assert strategy_for_layout(None) == DialogueTargetStrategy.FULL_MIX
    assert strategy_for_layout("quad") == DialogueTargetStrategy.FULL_MIX


def test_unlabeled_surround_assumes_center() -> None:
    assert strategy_for_layout(None, 8) == DialogueTargetStrategy.CENTER_CHANNEL
    assert strategy_for_layout(None, 6) == DialogueTargetStrategy.CENTER_CHANNEL


def test_default_channels() -> None:
    assert default_operation_channels(DialogueTargetStrategy.CENTER_CHANNEL) == ["FC"]
    assert default_operation_channels(DialogueTargetStrategy.FULL_MIX) == []
