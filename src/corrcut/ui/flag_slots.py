"""User-configurable colors and meanings for numpad flag slots."""

from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor

from corrcut.config import app_settings
from corrcut.domain.timeline_flag import FLAG_SLOTS

_KEY_PREFIX = "flag_slots"

FLAG_SLOT_DEFAULTS: dict[int, tuple[str, str]] = {
    1: ("#E44545", "Flag 1"),
    2: ("#E8C43A", "Flag 2"),
    3: ("#3DA5E8", "Flag 3"),
}

NUMPAD_SEQUENCES = {1: "Num+1", 2: "Num+2", 3: "Num+3"}

_DIGIT_KEYS = {
    int(Qt.Key.Key_1): 1,
    int(Qt.Key.Key_2): 2,
    int(Qt.Key.Key_3): 3,
}


@dataclass(frozen=True)
class FlagSlotStyle:
    slot: int
    color: QColor
    label: str

    def display_label(self) -> str:
        text = self.label.strip()
        return text if text else f"Flag {self.slot}"


def _settings():
    return app_settings()


def flag_slot_for_key(
    key: int,
    modifiers: object = Qt.KeyboardModifier.NoModifier,
    *,
    digits: bool = False,
) -> int | None:
    """Map a Qt key to flag slot 1–3.

    Numpad 1/2/3 (KeypadModifier) always count. Top-row digits only when
    *digits* is true, used in fullscreen where there is no text field.
    """
    slot = _DIGIT_KEYS.get(int(key))
    if slot is None:
        return None
    if digits:
        return slot
    if modifiers & Qt.KeyboardModifier.KeypadModifier:
        return slot
    return None


def resolve_flag_color(custom: str | None, slot_color: QColor) -> QColor:
    """Paint *custom* when it is a real color; otherwise the slot color."""
    if custom:
        parsed = QColor(custom)
        if parsed.isValid():
            parsed.setAlpha(255)
            return parsed
    color = QColor(slot_color)
    color.setAlpha(255)
    return color


def load_flag_slot_styles() -> dict[int, FlagSlotStyle]:
    settings = _settings()
    styles: dict[int, FlagSlotStyle] = {}
    for slot in FLAG_SLOTS:
        default_hex, default_label = FLAG_SLOT_DEFAULTS[slot]
        hex_color = str(settings.value(f"{_KEY_PREFIX}/{slot}/color", default_hex) or default_hex)
        label = str(settings.value(f"{_KEY_PREFIX}/{slot}/label", default_label) or default_label)
        color = QColor(hex_color)
        if not color.isValid():
            color = QColor(default_hex)
        styles[slot] = FlagSlotStyle(slot=slot, color=color, label=label)
    return styles


def save_flag_slot_styles(styles: dict[int, FlagSlotStyle]) -> None:
    settings = _settings()
    for slot in FLAG_SLOTS:
        style = styles.get(slot)
        if style is None:
            continue
        settings.setValue(f"{_KEY_PREFIX}/{slot}/color", style.color.name())
        settings.setValue(f"{_KEY_PREFIX}/{slot}/label", style.display_label())
