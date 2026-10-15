"""Per-operation channel scope for center-channel surround editing."""

from __future__ import annotations

FRONT_CHANNEL_NAMES: tuple[str, ...] = ("FC", "FL", "FR")


def editable_channel_names(layout: str | None, channels: int) -> list[str]:
    """Checkbox columns for censor regions: front bed plus surround (not LFE)."""
    from corrcut.domain.channel_layout import display_channel_labels

    labels = display_channel_labels(layout, channels)
    label_set = set(labels)
    out: list[str] = []
    for name in FRONT_CHANNEL_NAMES:
        if name in label_set:
            out.append(name)
    for name in labels:
        if name not in FRONT_CHANNEL_NAMES and name != "LFE":
            out.append(name)
    return out if out else list(FRONT_CHANNEL_NAMES)


def scope_channels_from_checks(**checks: bool) -> list[str]:
    out: list[str] = []
    for name, checked in checks.items():
        if checked:
            out.append(name)
    return out


def checks_from_scope_channels(
    channels: list[str],
    channel_names: list[str],
) -> dict[str, bool]:
    names = set(channels)
    return {name: name in names for name in channel_names}


def operation_targets_channel(
    channels: list[str],
    label: str,
    *,
    per_channel_editing: bool,
) -> bool:
    """Whether a filter op applies to a discrete channel pad in a multichannel layout."""
    if not per_channel_editing:
        return True
    return label in channels
