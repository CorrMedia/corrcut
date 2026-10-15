"""Choose a color and meaning for each numpad flag slot."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QColorDialog,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from corrcut.domain.timeline_flag import FLAG_SLOTS
from corrcut.ui.flag_slots import FlagSlotStyle, load_flag_slot_styles, save_flag_slot_styles


class FlagColorsDialog(QDialog):
    """Per-slot color + label. Bindings stay on numpad 1 / 2 / 3."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Flag colors")
        self.setModal(True)
        self._color_buttons: dict[int, QPushButton] = {}
        self._label_edits: dict[int, QLineEdit] = {}
        self._colors: dict[int, QColor] = {}

        root = QVBoxLayout(self)
        intro = QLabel(
            "Numpad 1, 2, and 3 drop a flag at the playhead — including while "
            "the video is playing fullscreen. Pick a color and meaning for each."
        )
        intro.setWordWrap(True)
        root.addWidget(intro)

        form = QFormLayout()
        styles = load_flag_slot_styles()
        for slot in FLAG_SLOTS:
            style = styles[slot]
            row = QWidget()
            row_layout = QHBoxLayout(row)
            row_layout.setContentsMargins(0, 0, 0, 0)
            color_btn = QPushButton()
            color_btn.setFixedSize(36, 24)
            color_btn.setCursor(Qt.CursorShape.PointingHandCursor)
            color_btn.setToolTip("Choose color")
            color_btn.clicked.connect(lambda _checked=False, s=slot: self._pick_color(s))
            self._color_buttons[slot] = color_btn
            self._colors[slot] = QColor(style.color)
            self._apply_color_button(slot)

            label_edit = QLineEdit(style.display_label())
            label_edit.setPlaceholderText(f"Meaning for numpad {slot}")
            label_edit.setMaxLength(40)
            self._label_edits[slot] = label_edit

            row_layout.addWidget(color_btn)
            row_layout.addWidget(label_edit, 1)
            form.addRow(f"Numpad {slot}", row)
        root.addLayout(form)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

    def _apply_color_button(self, slot: int) -> None:
        color = self._colors[slot]
        self._color_buttons[slot].setStyleSheet(
            f"background-color: {color.name()}; border: 1px solid #222; border-radius: 3px;"
        )

    def _pick_color(self, slot: int) -> None:
        chosen = QColorDialog.getColor(self._colors[slot], self, f"Flag {slot} color")
        if not chosen.isValid():
            return
        chosen.setAlpha(255)
        self._colors[slot] = chosen
        self._apply_color_button(slot)

    def styles_from_form(self) -> dict[int, FlagSlotStyle]:
        return {
            slot: FlagSlotStyle(
                slot=slot,
                color=QColor(self._colors[slot]),
                label=self._label_edits[slot].text(),
            )
            for slot in FLAG_SLOTS
        }

    def accept(self) -> None:  # noqa: D102
        save_flag_slot_styles(self.styles_from_form())
        super().accept()
