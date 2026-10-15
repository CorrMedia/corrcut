"""Dialogue-targeting strategy per channel layout (blueprint 5e)."""

from __future__ import annotations

from enum import Enum


class DialogueTargetStrategy(str, Enum):
    CENTER_CHANNEL = "center_channel"
    FULL_MIX = "full_mix"


def strategy_for_layout(channel_layout: str | None, channels: int = 0) -> DialogueTargetStrategy:
    """
    Auto-select v1 dialogue-targeting strategy from ffprobe channel_layout.

    5.1 / 6.1 / 7.1 (including wide-side) with discrete center -> center_channel (FC).
    Unlabeled 6+ channel streams assume 7.1/5.1 from channel count.
    Stereo and unknown -> full_mix.
    """
    if not channel_layout:
        if channels >= 6:
            from corrcut.domain.channel_layout import default_layout_for_channels

            channel_layout = default_layout_for_channels(channels)
        else:
            return DialogueTargetStrategy.FULL_MIX

    layout = channel_layout.strip().lower()

    if layout.startswith(("7.1", "5.1", "6.1")):
        return DialogueTargetStrategy.CENTER_CHANNEL

    if layout == "stereo":
        return DialogueTargetStrategy.FULL_MIX

    # Heuristic: layouts that include a center channel name
    if "fc" in layout.replace(".", "").split("+"):
        return DialogueTargetStrategy.CENTER_CHANNEL

    return DialogueTargetStrategy.FULL_MIX


def default_operation_channels(strategy: DialogueTargetStrategy) -> list[str]:
    """Channels scope for new censor Operations on this track."""
    if strategy == DialogueTargetStrategy.CENTER_CHANNEL:
        return ["FC"]
    return []
