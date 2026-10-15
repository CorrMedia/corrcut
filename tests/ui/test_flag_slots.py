from pathlib import Path

from PySide6.QtCore import QSettings, Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QApplication

from corrcut.ui.flag_slots import (
    FlagSlotStyle,
    flag_slot_for_key,
    load_flag_slot_styles,
    resolve_flag_color,
    save_flag_slot_styles,
)

_app = QApplication.instance() or QApplication([])


def test_resolve_flag_color_prefers_a_custom_color() -> None:
    slot = QColor("#e44545")
    assert resolve_flag_color("#00ff00", slot).name() == "#00ff00"
    assert resolve_flag_color(None, slot).name() == "#e44545"
    assert resolve_flag_color("nope", slot).name() == "#e44545"


def test_flag_slot_for_key_maps_numpad_and_digits() -> None:
    assert flag_slot_for_key(Qt.Key.Key_2, Qt.KeyboardModifier.KeypadModifier) == 2
    assert flag_slot_for_key(Qt.Key.Key_2) is None
    assert flag_slot_for_key(Qt.Key.Key_2, digits=True) == 2
    assert flag_slot_for_key(Qt.Key.Key_Space, digits=True) is None


def test_flag_slot_styles_round_trip(tmp_path: Path, monkeypatch) -> None:  # noqa: ANN001
    ini = tmp_path / "flags.ini"

    def _settings() -> QSettings:
        return QSettings(str(ini), QSettings.Format.IniFormat)

    monkeypatch.setattr("corrcut.ui.flag_slots._settings", _settings)
    save_flag_slot_styles(
        {
            1: FlagSlotStyle(1, QColor("#112233"), "Language"),
            2: FlagSlotStyle(2, QColor("#445566"), "Violence"),
            3: FlagSlotStyle(3, QColor("#778899"), "Review"),
        }
    )
    styles = load_flag_slot_styles()
    assert styles[1].display_label() == "Language"
    assert styles[1].color.name() == "#112233"
    assert styles[2].display_label() == "Violence"
