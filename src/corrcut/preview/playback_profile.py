"""Viewer filter for Edited preview.

CorrMedia lets a Jellyfin user turn categories and actions on or off. This
profile is that same choice for preview. It is stored in app settings, not in
the project or corr.json, so export still contains every edit.

An operation plays when the artist left it enabled, its action is on, and its
categories match:

- A word tag plays only when that word is selected. Turning a parent on selects
  every word under it; turning one word off leaves the others on.
- A group tag (``profanity``, ``gore``, …) plays when that group is selected
  or any of its words are still selected.
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from dataclasses import dataclass

from corrcut.domain.corr_categories import (
    CATEGORY_IDS,
    category_branches,
    category_child_ids,
    option_for_category_id,
    resolve_categories,
)
from corrcut.domain.operation import Operation, OperationType

_SETTINGS_KEY = "playback_profile/disabled"

# Authoring actions CorrMedia can apply. Legacy duck/gain values normalize to volume.
PLAYBACK_ACTIONS: tuple[tuple[str, str], ...] = (
    (OperationType.MUTE.value, "Mute"),
    (OperationType.VOLUME.value, "Volume"),
    (OperationType.BEEP.value, "Beep"),
    (OperationType.SKIP.value, "Skip"),
    (OperationType.BLANK.value, "Blank"),
    (OperationType.COVER.value, "Cover"),
    (OperationType.BLUR.value, "Blur"),
    (OperationType.PIXELATE.value, "Pixelate"),
    (OperationType.ZOOM.value, "Zoom"),
    (OperationType.CROP.value, "Crop"),
)
PLAYBACK_ACTION_IDS: frozenset[str] = frozenset(action_id for action_id, _label in PLAYBACK_ACTIONS)
_ACTION_LABELS: dict[str, str] = dict(PLAYBACK_ACTIONS)


def categories_selected(categories: list[str] | None, enabled: frozenset[str]) -> bool:
    """True when *categories* would play under the enabled catalog ids."""
    if not enabled:
        return False
    for cat_id in resolve_categories(categories):
        if cat_id in enabled:
            return True
        if category_child_ids(cat_id) & enabled:
            return True
    return False


@dataclass(frozen=True)
class PlaybackProfile:
    """Categories and actions turned off relative to "play every edit"."""

    disabled_categories: frozenset[str] = frozenset()
    disabled_actions: frozenset[str] = frozenset()

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "disabled_categories",
            frozenset(self.disabled_categories) & CATEGORY_IDS,
        )
        object.__setattr__(
            self,
            "disabled_actions",
            frozenset(self.disabled_actions) & PLAYBACK_ACTION_IDS,
        )

    @classmethod
    def full(cls) -> PlaybackProfile:
        """Every category and every action. Today's Edited playback."""
        return cls()

    @classmethod
    def none(cls) -> PlaybackProfile:
        """No category and no action. Edited playback matches the original."""
        return cls(disabled_categories=CATEGORY_IDS, disabled_actions=PLAYBACK_ACTION_IDS)

    @classmethod
    def from_selection(
        cls,
        *,
        categories: Iterable[str],
        actions: Iterable[str],
    ) -> PlaybackProfile:
        """Build a profile from the ids the viewer left on."""
        enabled_categories = frozenset(categories) & CATEGORY_IDS
        enabled_actions = frozenset(actions) & PLAYBACK_ACTION_IDS
        return cls(
            disabled_categories=CATEGORY_IDS - enabled_categories,
            disabled_actions=PLAYBACK_ACTION_IDS - enabled_actions,
        )

    def enabled_categories(self) -> frozenset[str]:
        return CATEGORY_IDS - self.disabled_categories

    def enabled_actions(self) -> frozenset[str]:
        return PLAYBACK_ACTION_IDS - self.disabled_actions

    def is_full(self) -> bool:
        return not self.disabled_categories and not self.disabled_actions

    def is_passthrough(self) -> bool:
        return not self.enabled_categories() or not self.enabled_actions()

    def without(
        self,
        *,
        categories: Iterable[str] = (),
        actions: Iterable[str] = (),
    ) -> PlaybackProfile:
        """Return a copy with more categories or actions turned off."""
        return PlaybackProfile(
            disabled_categories=self.disabled_categories | frozenset(categories),
            disabled_actions=self.disabled_actions | frozenset(actions),
        )

    def matches(self, op: Operation) -> bool:
        """True when Edited preview should apply *op*."""
        if not op.enabled:
            return False
        if op.type.value not in self.enabled_actions():
            return False
        return categories_selected(op.categories, self.enabled_categories())

    def button_label(self) -> str:
        if self.is_full():
            return "All"
        if self.is_passthrough():
            return "None"
        return "Custom"

    def status_text(self) -> str:
        if self.is_full():
            return "Edited playback applies every category and action."
        if self.is_passthrough():
            return "Nothing is selected, so Edited playback matches the original."
        lines = ["Edited playback uses this CorrMedia filter."]
        actions = self._disabled_action_text()
        categories = self._disabled_category_text()
        if actions:
            lines.append(actions)
        if categories:
            lines.append(categories)
        return "\n".join(lines)

    def encode(self) -> str:
        """JSON for app settings. A full profile encodes as an empty string."""
        if self.is_full():
            return ""
        payload = {
            "disabled_categories": sorted(self.disabled_categories),
            "disabled_actions": sorted(self.disabled_actions),
        }
        return json.dumps(payload, separators=(",", ":"))

    @classmethod
    def decode(cls, raw: str | None) -> PlaybackProfile:
        text = "" if raw is None else str(raw).strip()
        if not text:
            return cls.full()
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            return cls.full()
        if not isinstance(data, dict):
            return cls.full()
        categories = data.get("disabled_categories", [])
        actions = data.get("disabled_actions", [])
        if not isinstance(categories, list) or not isinstance(actions, list):
            return cls.full()
        return cls(
            disabled_categories=frozenset(str(item) for item in categories),
            disabled_actions=frozenset(str(item) for item in actions),
        )

    def _disabled_action_text(self) -> str:
        if not self.disabled_actions:
            return ""
        labels = [
            _ACTION_LABELS[action_id]
            for action_id, _label in PLAYBACK_ACTIONS
            if action_id in self.disabled_actions
        ]
        return "Actions off: " + ", ".join(labels)

    def _disabled_category_text(self) -> str:
        hidden = self.disabled_categories
        if not hidden:
            return ""
        labels: list[str] = []
        seen: set[str] = set()
        for _group, branches in category_branches():
            for branch in branches:
                parent = branch.option
                child_ids = [child.id for child in branch.children]
                if parent.id in seen:
                    continue
                if child_ids and parent.id in hidden and set(child_ids) <= hidden:
                    labels.append(parent.label)
                    seen.add(parent.id)
                    seen.update(child_ids)
                    continue
                if child_ids:
                    for child in branch.children:
                        if child.id in hidden and child.id not in seen:
                            label = child.label
                            parent_option = option_for_category_id(parent.id)
                            if parent_option is not None:
                                label = f"{parent_option.label}: {child.label}"
                            labels.append(label)
                            seen.add(child.id)
                    continue
                if parent.id in hidden:
                    labels.append(parent.label)
                    seen.add(parent.id)
        if not labels:
            return ""
        if len(labels) > 6:
            return f"{len(hidden)} categories off"
        return "Off: " + ", ".join(labels)


def load_playback_profile() -> PlaybackProfile:
    """Profile saved for this machine. Missing settings mean every edit."""
    from corrcut.config import app_settings

    raw = app_settings().value(_SETTINGS_KEY, "")
    return PlaybackProfile.decode("" if raw is None else str(raw))


def save_playback_profile(profile: PlaybackProfile) -> None:
    """Remember *profile*. A full profile clears the saved filter."""
    from corrcut.config import app_settings

    settings = app_settings()
    encoded = profile.encode()
    if not encoded:
        settings.remove(_SETTINGS_KEY)
        return
    settings.setValue(_SETTINGS_KEY, encoded)
