"""Shared dock table behavior."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QComboBox, QTableWidget, QTableWidgetItem

from corrcut.ui.table_helpers import configure_panel_table

_app = QApplication.instance() or QApplication([])


def test_panel_table_arrow_keys_scroll_selection() -> None:
    table = QTableWidget(12, 1)
    for row in range(12):
        table.setItem(row, 0, QTableWidgetItem(f"row {row}"))
    configure_panel_table(table)
    table.setFixedHeight(90)
    table.show()
    table.setCurrentCell(0, 0)
    table.setFocus()
    _app.processEvents()

    for _ in range(4):
        QTest.keyClick(table, Qt.Key.Key_Down)
        _app.processEvents()

    assert table.currentRow() == 4
    assert table.verticalScrollBar().value() > 0


def test_panel_table_arrow_keys_work_with_cell_widgets() -> None:
    table = QTableWidget(5, 2)
    for row in range(5):
        table.setCellWidget(row, 0, QComboBox())
        table.setItem(row, 1, QTableWidgetItem(f"row {row}"))
    configure_panel_table(table)
    table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
    table.show()
    table.setCurrentCell(0, 1)
    table.setFocus()
    _app.processEvents()

    for expected in (1, 2, 3):
        QTest.keyClick(table, Qt.Key.Key_Down)
        _app.processEvents()
        assert table.currentRow() == expected
        assert isinstance(_app.focusWidget(), QTableWidget)
