"""Category search matching and chip picker helpers."""

from fractions import Fraction

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

from corrcut.domain.dialogue_target import DialogueTargetStrategy
from corrcut.domain.operation import Operation, OperationType
from corrcut.domain.time_point import TimePoint
from corrcut.ui.category_chips import (
    CategoryChipsPicker,
    category_label_for,
    category_matches_query,
)
from corrcut.ui.operations_panel import OperationsPanel

_app = QApplication.instance() or QApplication([])


def test_category_matches_query_tokens() -> None:
    assert category_matches_query("word_fuck", "Profanity: f*ck", "fuck")
    assert category_matches_query("word_fuck", "Profanity: f*ck", "f*ck")
    assert category_matches_query("gore", "Violence: Gore", "violence")
    assert not category_matches_query("gore", "Violence: Gore", "fuck")


def test_category_label_for_known_id() -> None:
    label = category_label_for("word_fuck")
    assert "profanity" in label.lower()
    assert "f" in label.lower()


def test_category_search_keeps_focus_while_suggestions_open(qtbot) -> None:
    picker = CategoryChipsPicker()
    qtbot.addWidget(picker)
    picker.show()
    qtbot.waitExposed(picker)
    picker.set_categories(["other"])
    picker.set_audio_filter(True)

    picker.search_field.setFocus()
    qtbot.wait(10)
    assert picker.search_field.hasFocus()
    assert picker._popup.isVisible()  # noqa: SLF001

    qtbot.keyClicks(picker.search_field, "fuck")
    assert picker.search_field.text() == "fuck"
    assert picker.search_field.hasFocus()
    assert picker._popup.isVisible()  # noqa: SLF001
    assert any(
        item is not None and item.data(Qt.ItemDataRole.UserRole) == "word_fuck"
        for i in range(picker._popup.count())  # noqa: SLF001
        for item in [picker._popup.item(i)]  # noqa: SLF001
    )


def test_category_search_typing_works_inside_operations_panel(qtbot) -> None:
    panel = OperationsPanel()
    qtbot.addWidget(panel)
    op = Operation.create(
        OperationType.MUTE,
        0,
        ["FC"],
        TimePoint(Fraction(1)),
        TimePoint(Fraction(2)),
    )
    panel.load_operations(
        [op],
        strategy=DialogueTargetStrategy.CENTER_CHANNEL,
        channel_layout="5.1",
        channels=6,
    )
    panel.update_operation_editor(op)
    panel.show()
    qtbot.waitExposed(panel)

    search = panel._categories.search_field
    search.setFocus()
    qtbot.wait(10)
    qtbot.keyClicks(search, "ass")
    assert search.text() == "ass"
    assert search.hasFocus()
