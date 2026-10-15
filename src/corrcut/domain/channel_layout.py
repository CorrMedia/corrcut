"""Normalized channel labels per ffprobe layout (Phase 1 / export parity)."""

from __future__ import annotations

# FFmpeg channel order per layout (must match channelsplit/join for that layout).
_LAYOUT_CHANNEL_LABELS: dict[str, list[str]] = {
    "5.1": ["FL", "FR", "FC", "LFE", "BL", "BR"],
    "5.1(side)": ["FL", "FR", "FC", "LFE", "SL", "SR"],
    "6.1": ["FL", "FR", "FC", "LFE", "BC", "SL", "SR"],
    "7.1": ["FL", "FR", "FC", "LFE", "BL", "BR", "SL", "SR"],
    "7.1(wide)": ["FL", "FR", "FC", "LFE", "BL", "BR", "FLC", "FRC"],
    "7.1(wide-side)": ["FL", "FR", "FC", "LFE", "FLC", "FRC", "SL", "SR"],
    "stereo": ["FL", "FR"],
    "mono": ["FC"],
}


def known_layout_channel_labels(layout: str | None, channels: int) -> list[str] | None:
    """Exact FFmpeg layout labels, or None if unsupported for split/join."""
    if not layout:
        return None
    key = layout.strip().lower()
    labels = _LAYOUT_CHANNEL_LABELS.get(key)
    if labels is None or len(labels) != channels:
        return None
    return list(labels)


def layout_channel_labels(layout: str | None, channels: int) -> list[str]:
    """Return ordered channel labels for UI; generic CHn if layout unknown."""
    known = known_layout_channel_labels(layout, channels)
    if known is not None:
        return known
    if channels == 2:
        return ["FL", "FR"]
    if channels == 1:
        return ["FC"]
    return [f"CH{i + 1}" for i in range(channels)]


def display_channel_labels(layout: str | None, channels: int) -> list[str]:
    """Speaker-map labels, inferring 7.1/5.1 when ffprobe omits a named layout.

    Atmos / unlabeled 8+ channel streams still show the 7.1 bed rather than a
    stereo pair or CH1…CH16 placeholders.
    """
    known = known_layout_channel_labels(layout, channels)
    if known is not None:
        return known
    inferred = (layout or default_layout_for_channels(channels)).strip().lower()
    if inferred.startswith("7.1") or channels >= 8:
        return list(_LAYOUT_CHANNEL_LABELS.get(inferred) or _LAYOUT_CHANNEL_LABELS["7.1"])
    if inferred.startswith("6.1") or channels == 7:
        return list(_LAYOUT_CHANNEL_LABELS["6.1"])
    if inferred.startswith("5.1") or channels == 6:
        return list(_LAYOUT_CHANNEL_LABELS.get(inferred) or _LAYOUT_CHANNEL_LABELS["5.1"])
    return layout_channel_labels(layout, channels)


def default_layout_for_channels(channels: int) -> str:
    """Layout name to assume when ffprobe omits channel_layout.

    Surround counts map to 7.1/5.1. Unknown or 2-channel files stay stereo so
    we never invent a silent center channel.
    """
    if channels >= 8:
        return "7.1"
    if channels == 7:
        return "6.1"
    if channels == 6:
        return "5.1"
    if channels == 1:
        return "mono"
    return "stereo"


# Side and back surrounds stand in for each other when a layout has only one pair.
_SURROUND_ALIASES: dict[str, tuple[str, ...]] = {
    "SL": ("BL",),
    "BL": ("SL",),
    "SR": ("BR",),
    "BR": ("SR",),
}


def map_channel_name(name: str, target_labels: list[str]) -> str | None:
    """Map a stored channel onto a label that exists in *target_labels*."""
    if name in target_labels:
        return name
    for alias in _SURROUND_ALIASES.get(name, ()):
        if alias in target_labels:
            return alias
    return None


def channels_for_layout(
    stored: list[str],
    layout: str | None,
    channels: int,
) -> list[str] | None:
    """Channels to apply on this layout.

    An empty list means the whole mix (stereo has no center, so a center mute
    covers FL+FR). None means nothing on this layout corresponds to the edit,
    so a side-surround mute does not silence a stereo track.
    """
    if not stored:
        return []
    labels = display_channel_labels(layout, channels)
    present = set(labels)
    mapped: list[str] = []
    folded_dialogue = False
    for name in stored:
        hit = map_channel_name(name, labels)
        if hit is not None:
            if hit not in mapped:
                mapped.append(hit)
        elif name == "FC" and "FC" not in present:
            folded_dialogue = True
    if folded_dialogue and not mapped:
        return []
    if not mapped:
        return None
    chosen = set(mapped)
    return [name for name in labels if name in chosen]


def replace_layout_channels(
    stored: list[str],
    checked: list[str],
    layout: str | None,
    channels: int,
) -> list[str]:
    """Replace the channels this layout can show, and keep the rest."""
    labels = display_channel_labels(layout, channels)
    present = set(labels)
    kept: list[str] = []
    for name in stored:
        hit = map_channel_name(name, labels)
        if hit is None and name not in present:
            kept.append(name)
    for name in checked:
        if name not in kept:
            kept.append(name)
    return kept
