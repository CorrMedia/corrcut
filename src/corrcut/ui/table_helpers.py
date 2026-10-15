"""Shared QTableWidget settings for dock panels."""

from __future__ import annotations

from collections.abc import Callable
from typing import TypeVar

from PySide6.QtCore import QEvent, QObject, Qt
from PySide6.QtGui import QKeyEvent
from PySide6.QtWidgets import QAbstractItemView, QApplication, QTableWidget, QWidget

from corrcut.engine.subtitles.display import (
    DEFAULT_MIN_DISPLAY_SECONDS,
    DEFAULT_POST_DISPLAY_SECONDS,
)

T = TypeVar("T")


def mark_cell_widget_non_focusable(widget: QWidget) -> QWidget:
    """Keep keyboard navigation on the table instead of embedded controls."""
    widget.setFocusPolicy(Qt.FocusPolicy.NoFocus)
    for child in widget.findChildren(QWidget):
        child.setFocusPolicy(Qt.FocusPolicy.NoFocus)
    return widget


def _item_column(table: QTableWidget, row: int) -> int:
    current = max(0, table.currentColumn())
    if table.item(row, current) is not None:
        return current
    for column in range(table.columnCount()):
        if table.item(row, column) is not None:
            return column
    return 0


def _scroll_row_into_view(table: QTableWidget, row: int) -> None:
    for column in range(table.columnCount()):
        item = table.item(row, column)
        if item is not None:
            table.scrollToItem(item, QAbstractItemView.ScrollHint.EnsureVisible)
            return
    index = table.model().index(row, 0)
    table.scrollTo(index, QAbstractItemView.ScrollHint.EnsureVisible)


def scroll_row_to_center(table: QTableWidget, row: int) -> None:
    for column in range(table.columnCount()):
        item = table.item(row, column)
        if item is not None:
            table.scrollToItem(item, QAbstractItemView.ScrollHint.PositionAtCenter)
            return
    index = table.model().index(row, 0)
    table.scrollTo(index, QAbstractItemView.ScrollHint.PositionAtCenter)


def timed_item_at_seconds[T](
    items: list[T],
    seconds: float,
    *,
    start_seconds: Callable[[T], float],
    end_seconds: Callable[[T], float],
) -> T | None:
    """Return the first item whose [start, end) range contains ``seconds``."""
    for item in items:
        if start_seconds(item) <= seconds < end_seconds(item):
            return item
    return None


def timed_item_for_playhead[T](
    items: list[T],
    seconds: float,
    *,
    start_seconds: Callable[[T], float],
    end_seconds: Callable[[T], float],
    post_display_seconds: float = DEFAULT_POST_DISPLAY_SECONDS,
    min_display_seconds: float = DEFAULT_MIN_DISPLAY_SECONDS,
) -> T | None:
    """
    Return the timed item visible at ``seconds``.

    Matches active [start, end) windows first, then items lingering briefly after
    their end — the same timing model used for subtitle cue display.
    """
    if not items:
        return None

    for item in items:
        start = start_seconds(item)
        end = end_seconds(item)
        if start <= seconds < end:
            return item

    for item in reversed(items):
        start = start_seconds(item)
        end = end_seconds(item)
        effective_end = max(end, start + min_display_seconds)
        visible_until = effective_end + post_display_seconds
        if end <= seconds < visible_until:
            return item

    return None


def select_playhead_row(
    table: QTableWidget,
    row: int,
    *,
    column: int | None = None,
    scroll: bool = True,
) -> None:
    """Select a table row and optionally scroll it to the vertical center."""
    target_column = column if column is not None else _item_column(table, row)
    table.setCurrentCell(row, target_column)
    table.selectRow(row)
    if scroll:
        scroll_row_to_center(table, row)


class _PanelTableKeyNavigationFilter(QObject):
    """Keep Up/Down/Home/End moving row selection and scrolling it into view."""

    def __init__(self, table: QTableWidget) -> None:
        super().__init__(table)
        self._table = table

    def _table_has_focus(self, table: QTableWidget) -> bool:
        focus = QApplication.focusWidget()
        if focus is None:
            return False
        return focus is table or table.isAncestorOf(focus)

    def _handle_navigation_key(self, table: QTableWidget, event: QKeyEvent) -> bool:
        if event.isAutoRepeat():
            return False
        if table.state() == QAbstractItemView.State.EditingState:
            return False
        key = event.key()
        if key not in (
            Qt.Key.Key_Up,
            Qt.Key.Key_Down,
            Qt.Key.Key_Home,
            Qt.Key.Key_End,
        ):
            return False
        row_count = table.rowCount()
        if row_count == 0:
            return True
        current = table.currentRow()
        if key == Qt.Key.Key_Up:
            target = max(0, current - 1) if current >= 0 else 0
        elif key == Qt.Key.Key_Down:
            target = min(row_count - 1, current + 1) if current >= 0 else 0
        elif key == Qt.Key.Key_Home:
            target = 0
        else:
            target = row_count - 1
        if target == current:
            return True
        column = _item_column(table, target)
        table.setCurrentCell(target, column)
        table.selectRow(target)
        _scroll_row_into_view(table, target)
        table.setFocus(Qt.FocusReason.OtherFocusReason)
        return True

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        table = getattr(self, "_table", None)
        if table is None:
            return False
        try:
            if event.type() != QEvent.Type.KeyPress or not isinstance(event, QKeyEvent):
                return False
            if watched is not table and not (
                isinstance(watched, QWidget) and table.isAncestorOf(watched)
            ):
                return False
            if self._handle_navigation_key(table, event):
                return True
        except RuntimeError:
            return False
        return False


class _PanelTableViewportFilter(QObject):
    """Block horizontal wheel scroll and drag-to-scroll selection on dock tables."""

    def __init__(self, table: QTableWidget) -> None:
        super().__init__(table)
        self._table = table

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        table = getattr(self, "_table", None)
        if table is None:
            return False
        try:
            viewport = table.viewport()
        except RuntimeError:
            return False
        if watched is not viewport:
            return False
        if event.type() == QEvent.Type.Wheel:
            delta = event.angleDelta()
            if delta.x() != 0 and abs(delta.x()) >= abs(delta.y()):
                return True
        return bool(
            event.type() == QEvent.Type.MouseMove and event.buttons() & Qt.MouseButton.LeftButton
        )


def configure_panel_table(table: QTableWidget) -> None:
    """Keep dock tables wheel-scrollable without drag-scrolling or horizontal pan."""
    table.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
    table.setEditTriggers(QAbstractItemView.EditTrigger.DoubleClicked)
    table.setDragDropMode(QAbstractItemView.DragDropMode.NoDragDrop)
    table.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
    header = table.horizontalHeader()
    header.setSectionsMovable(False)
    table.installEventFilter(_PanelTableKeyNavigationFilter(table))
    table.viewport().installEventFilter(_PanelTableViewportFilter(table))
