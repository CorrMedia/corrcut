"""Shared beep tone preset combo for timeline toolbar."""

from __future__ import annotations

from PySide6.QtWidgets import QComboBox

from corrcut.domain.operation import BEEP_PRESETS, beep_preset_index_for_freq


def populate_beep_tone_combo(combo: QComboBox, freq: float) -> None:
    combo.blockSignals(True)
    combo.clear()
    for label, preset_hz in BEEP_PRESETS:
        combo.addItem(label, preset_hz)
    preset_index = beep_preset_index_for_freq(freq)
    if preset_index >= 0:
        combo.setCurrentIndex(preset_index)
    else:
        combo.insertItem(0, f"Custom ({int(freq)} Hz)", freq)
        combo.setCurrentIndex(0)
    combo.blockSignals(False)
