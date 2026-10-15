"""Search + removable chips for corr.json category tags."""

from __future__ import annotations

from PySide6.QtCore import QEvent, QPoint, QRect, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QKeyEvent
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QLayout,
    QLayoutItem,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QSizePolicy,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from corrcut.domain.corr_categories import (
    OTHER_ID,
    CategoryOption,
    category_applies_to_media,
    combo_entries,
    option_for_category_id,
    resolve_categories,
)

_CHIP_STYLE = """
QFrame#categoryChip {
    background: #2a2e36;
    border: 1px solid #4a505c;
    border-radius: 4px;
}
QLabel#categoryChipLabel {
    color: #e0e0e0;
    padding: 0 2px 0 6px;
}
QToolButton#categoryChipRemove {
    color: #aaa;
    background: transparent;
    border: none;
    padding: 0 6px 0 2px;
    font-size: 12px;
}
QToolButton#categoryChipRemove:hover {
    color: #fff;
}
"""

_FIELD_HEIGHT = 30
_POPUP_MAX_ROWS = 6
_POPUP_ROW_HEIGHT = 30


def category_matches_query(cat_id: str, label: str, query: str) -> bool:
    """True when *query* matches a category id or display label."""
    needle = query.strip().lower().replace("*", "").replace("_", " ")
    if not needle:
        return True
    haystack = f"{cat_id} {label}".lower().replace("*", "").replace("_", " ")
    return all(token in haystack for token in needle.split())


def category_label_for(cat_id: str) -> str:
    option = option_for_category_id(cat_id)
    if option is not None:
        return option.combo_label()
    return cat_id


class _FlowLayout(QLayout):
    """Wrap children left-to-right like a tag cloud."""

    def __init__(self, parent: QWidget | None = None, *, spacing: int = 4) -> None:
        super().__init__(parent)
        self._items: list[QLayoutItem] = []
        self._spacing = spacing
        self.setContentsMargins(0, 0, 0, 0)

    def addItem(self, item: QLayoutItem) -> None:  # noqa: N802
        self._items.append(item)

    def count(self) -> int:
        return len(self._items)

    def itemAt(self, index: int) -> QLayoutItem | None:  # noqa: N802
        if 0 <= index < len(self._items):
            return self._items[index]
        return None

    def takeAt(self, index: int) -> QLayoutItem | None:  # noqa: N802
        if 0 <= index < len(self._items):
            return self._items.pop(index)
        return None

    def expandingDirections(self) -> Qt.Orientation:  # noqa: N802
        return Qt.Orientation(0)

    def hasHeightForWidth(self) -> bool:  # noqa: N802
        return True

    def heightForWidth(self, width: int) -> int:  # noqa: N802
        return self._do_layout(QRect(0, 0, width, 0), test_only=True)

    def setGeometry(self, rect: QRect) -> None:  # noqa: N802
        super().setGeometry(rect)
        self._do_layout(rect, test_only=False)

    def sizeHint(self) -> QSize:  # noqa: N802
        return self.minimumSize()

    def minimumSize(self) -> QSize:  # noqa: N802
        size = QSize()
        for item in self._items:
            size = size.expandedTo(item.minimumSize())
        left, top, right, bottom = self.getContentsMargins()
        size += QSize(left + right, top + bottom)
        return size

    def _do_layout(self, rect: QRect, *, test_only: bool) -> int:
        left, top, right, bottom = self.getContentsMargins()
        effective = rect.adjusted(left, top, -right, -bottom)
        x = effective.x()
        y = effective.y()
        line_height = 0
        for item in self._items:
            widget = item.widget()
            space_x = self._spacing
            space_y = self._spacing
            next_x = x + item.sizeHint().width() + space_x
            if line_height and next_x - space_x > effective.right() + 1:
                x = effective.x()
                y = y + line_height + space_y
                next_x = x + item.sizeHint().width() + space_x
                line_height = 0
            if not test_only:
                item.setGeometry(QRect(QPoint(x, y), item.sizeHint()))
            x = next_x
            line_height = max(line_height, item.sizeHint().height())
        return y + line_height - rect.y() + bottom


class _CategoryChip(QFrame):
    remove_requested = Signal(str)

    def __init__(self, cat_id: str, label: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.cat_id = cat_id
        self.setObjectName("categoryChip")
        self.setStyleSheet(_CHIP_STYLE)
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(0)
        text = QLabel(label)
        text.setObjectName("categoryChipLabel")
        text.setToolTip(cat_id)
        remove = QToolButton()
        remove.setObjectName("categoryChipRemove")
        remove.setText("×")
        remove.setCursor(Qt.CursorShape.PointingHandCursor)
        remove.setToolTip("Remove category")
        remove.clicked.connect(lambda: self.remove_requested.emit(self.cat_id))
        row.addWidget(text)
        row.addWidget(remove)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)


class CategoryChipsPicker(QWidget):
    """Selected category chips plus a search field that pops a filtered list."""

    categories_changed = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._selected: list[str] = []
        self._audio_filter = True
        self._loading = False
        self._catalog: list[CategoryOption] = [
            entry for entry in combo_entries() if entry is not None
        ]

        self._chips_host = QWidget()
        self._chips_layout = _FlowLayout(self._chips_host, spacing=4)
        self._chips_host.setLayout(self._chips_layout)
        self._chips_host.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Maximum
        )

        self._search = QLineEdit()
        self._search.setPlaceholderText("Add category…")
        self._search.setClearButtonEnabled(True)
        self._search.setMinimumHeight(_FIELD_HEIGHT)
        self._search.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self._search.textChanged.connect(self._on_search_text_changed)
        self._search.installEventFilter(self)

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(4)
        root.addWidget(self._search)
        root.addWidget(self._chips_host)

        self._popup = QListWidget()
        # Tool + no-focus: a Qt.Popup steals activation and the search field
        # can no longer receive typing.
        self._popup.setWindowFlags(
            Qt.WindowType.Tool
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowDoesNotAcceptFocus
        )
        self._popup.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self._popup.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._popup.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._popup.setMaximumHeight(_POPUP_MAX_ROWS * _POPUP_ROW_HEIGHT + 4)
        self._popup.itemClicked.connect(self._on_popup_item_clicked)
        self._popup.hide()
        self._hide_popup_timer = QTimer(self)
        self._hide_popup_timer.setSingleShot(True)
        self._hide_popup_timer.setInterval(0)
        self._hide_popup_timer.timeout.connect(self._hide_popup_if_search_unfocused)

    @property
    def search_field(self) -> QLineEdit:
        return self._search

    def selected_ids(self) -> list[str]:
        return list(self._selected)

    def set_audio_filter(self, audio: bool) -> None:
        self._audio_filter = bool(audio)
        if self._popup.isVisible():
            self._refresh_popup()

    def set_categories(self, category_ids: list[str]) -> None:
        self._loading = True
        try:
            self._selected = resolve_categories(category_ids)
            self._rebuild_chips()
            self._search.clear()
            self._hide_popup()
        finally:
            self._loading = False

    def eventFilter(self, watched: object, event: object) -> bool:  # noqa: N802
        if watched is self._search and isinstance(event, QEvent):
            if event.type() == QEvent.Type.KeyPress and isinstance(event, QKeyEvent):
                key = event.key()
                if key in (Qt.Key.Key_Down, Qt.Key.Key_Up) and self._popup.isVisible():
                    self._move_popup_selection(1 if key == Qt.Key.Key_Down else -1)
                    return True
                if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter) and self._popup.isVisible():
                    item = self._popup.currentItem()
                    if item is not None:
                        self._accept_popup_item(item)
                        return True
                if key == Qt.Key.Key_Escape and self._popup.isVisible():
                    self._hide_popup()
                    return True
                if key == Qt.Key.Key_Backspace and not self._search.text() and self._selected:
                    self._remove_category(self._selected[-1])
                    return True
            elif event.type() == QEvent.Type.FocusIn:
                self._hide_popup_timer.stop()
                self._refresh_popup(show_if_empty=True)
            elif event.type() == QEvent.Type.FocusOut:
                # Defer so a click on a suggestion can land before we hide.
                self._hide_popup_timer.start()
        return super().eventFilter(watched, event)  # type: ignore[arg-type]

    def hideEvent(self, event) -> None:  # type: ignore[no-untyped-def]  # noqa: N802
        self._hide_popup_timer.stop()
        self._hide_popup()
        super().hideEvent(event)

    def _hide_popup_if_search_unfocused(self) -> None:
        if not self._search.hasFocus():
            self._hide_popup()

    def _on_search_text_changed(self, _text: str) -> None:
        if self._loading:
            return
        self._refresh_popup(show_if_empty=True)

    def _candidates(self, query: str) -> list[CategoryOption]:
        selected = set(self._selected)
        searching = bool(query.strip())
        out: list[CategoryOption] = []
        for option in self._catalog:
            if option.id in selected:
                continue
            if not searching and not category_applies_to_media(
                option, audio=self._audio_filter
            ):
                continue
            if not category_matches_query(option.id, option.combo_label(), query):
                continue
            out.append(option)
        return out

    def _refresh_popup(self, *, show_if_empty: bool = False) -> None:
        query = self._search.text()
        if not show_if_empty and not query.strip() and not self._search.hasFocus():
            self._hide_popup()
            return
        rows = self._candidates(query)
        self._popup.clear()
        for option in rows:
            item = QListWidgetItem(option.combo_label())
            item.setData(Qt.ItemDataRole.UserRole, option.id)
            item.setToolTip(option.id)
            self._popup.addItem(item)
        if not rows:
            self._hide_popup()
            return
        self._popup.setCurrentRow(0)
        self._show_popup()

    def _show_popup(self) -> None:
        row_h = _POPUP_ROW_HEIGHT
        if self._popup.count() > 0:
            hinted = self._popup.sizeHintForRow(0)
            if hinted > 0:
                row_h = hinted
        visible = min(max(self._popup.count(), 1), _POPUP_MAX_ROWS)
        height = visible * row_h + 4
        self._popup.setMaximumHeight(_POPUP_MAX_ROWS * row_h + 4)
        width = max(self._search.width(), 180)
        self._popup.resize(width, height)
        global_pos = self._search.mapToGlobal(QPoint(0, self._search.height()))
        self._popup.move(global_pos)
        self._popup.show()
        # Keep caret in the search field even if the OS activates the tool window.
        if not self._search.hasFocus():
            self._search.setFocus(Qt.FocusReason.OtherFocusReason)

    def _hide_popup(self) -> None:
        self._hide_popup_timer.stop()
        self._popup.hide()

    def _move_popup_selection(self, delta: int) -> None:
        row = self._popup.currentRow() + delta
        row = max(0, min(self._popup.count() - 1, row))
        self._popup.setCurrentRow(row)

    def _on_popup_item_clicked(self, item: QListWidgetItem) -> None:
        self._accept_popup_item(item)

    def _accept_popup_item(self, item: QListWidgetItem) -> None:
        cat_id = item.data(Qt.ItemDataRole.UserRole)
        if not cat_id:
            return
        self._add_category(str(cat_id))

    def _add_category(self, cat_id: str) -> None:
        resolved = resolve_categories([*self._selected, cat_id])
        if resolved == self._selected:
            self._search.clear()
            self._hide_popup()
            return
        # Drop the default "other" chip when the user picks a real tag.
        if OTHER_ID in resolved and any(cid != OTHER_ID for cid in resolved):
            resolved = [cid for cid in resolved if cid != OTHER_ID]
        self._selected = resolved
        self._rebuild_chips()
        self._search.clear()
        self._hide_popup()
        if not self._loading:
            self.categories_changed.emit()

    def _remove_category(self, cat_id: str) -> None:
        remaining = [cid for cid in self._selected if cid != cat_id]
        self._selected = resolve_categories(remaining)
        self._rebuild_chips()
        if self._popup.isVisible():
            self._refresh_popup(show_if_empty=True)
        if not self._loading:
            self.categories_changed.emit()

    def _rebuild_chips(self) -> None:
        while self._chips_layout.count():
            item = self._chips_layout.takeAt(0)
            if item is None:
                break
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        for cat_id in self._selected:
            chip = _CategoryChip(cat_id, category_label_for(cat_id), self._chips_host)
            chip.remove_requested.connect(self._remove_category)
            self._chips_layout.addWidget(chip)
        self._chips_host.updateGeometry()
        self.updateGeometry()
