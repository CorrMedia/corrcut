"""Painted subtitle cue list. Rows are drawn, not one widget per cue."""

from __future__ import annotations

from collections.abc import Callable
from html import escape

from PySide6.QtCore import (
    QAbstractItemModel,
    QAbstractListModel,
    QModelIndex,
    QPersistentModelIndex,
    QPoint,
    QRect,
    QSize,
    Qt,
    Signal,
)
from PySide6.QtGui import QColor, QMouseEvent, QPainter, QPalette, QTextDocument
from PySide6.QtWidgets import (
    QAbstractItemView,
    QLineEdit,
    QListView,
    QStyle,
    QStyledItemDelegate,
    QStyleOptionViewItem,
    QWidget,
)

from corrcut.domain.subtitle_cue import SubtitleCue
from corrcut.engine.subtitles.cue_edit import cue_can_reset_to_generated

ROW_HEIGHT = 28
_BUTTON = 22
_BUTTON_GAP = 2
_BUTTON_PAD = 4
_ESCAPE_MARKERS = ("\\n", "\\r")
_ESCAPE_COLOR = "#5ba3d9"
_RESET_COLOR = QColor("#8a8a8a")
_RESET_DISABLED = QColor("#555555")
_DELETE_COLOR = QColor("#d64545")
_NO_PARENT = QModelIndex()


def cue_text_for_table(text: str) -> str:
    return text.replace("\r\n", "\\n").replace("\n", "\\n").replace("\r", "\\n")


def cue_text_from_table(text: str) -> str:
    return text.replace("\\n", "\n").replace("\\r", "\r")


def _display_html(display: str) -> str:
    parts: list[str] = []
    index = 0
    while index < len(display):
        marker = next(
            (token for token in _ESCAPE_MARKERS if display.startswith(token, index)),
            None,
        )
        if marker is not None:
            parts.append(
                f'<span style="color:{_ESCAPE_COLOR}; font-weight:600;">{marker}</span>'
            )
            index += len(marker)
            continue
        next_escape = len(display)
        for token in _ESCAPE_MARKERS:
            found = display.find(token, index + 1)
            if found != -1:
                next_escape = min(next_escape, found)
        parts.append(escape(display[index:next_escape]))
        index = next_escape
    return "".join(parts)


def action_rects(rect: QRect) -> tuple[QRect, QRect]:
    """Reset button, then delete button, anchored to the right of *rect*."""
    top = rect.center().y() - _BUTTON // 2
    delete = QRect(rect.right() - _BUTTON_PAD - _BUTTON + 1, top, _BUTTON, _BUTTON)
    reset = QRect(delete.left() - _BUTTON_GAP - _BUTTON, top, _BUTTON, _BUTTON)
    return reset, delete


class SubtitleCueListModel(QAbstractListModel):
    def __init__(self, parent: QListView | None = None) -> None:
        super().__init__(parent)
        self._cues: list[SubtitleCue] = []
        self._visible: list[int] = []
        self._source_by_id: dict[str, int] = {}
        self._row_by_id: dict[str, int] = {}

    def set_cues(
        self,
        cues: list[SubtitleCue],
        *,
        predicate: Callable[[SubtitleCue], bool] | None = None,
    ) -> None:
        self.beginResetModel()
        self._cues = list(cues)
        self._apply_visible(predicate)
        self.endResetModel()

    def update_cue(
        self,
        cue: SubtitleCue,
        *,
        predicate: Callable[[SubtitleCue], bool] | None = None,
    ) -> None:
        source = self._source_by_id.get(cue.id, -1)
        if source < 0:
            return
        self._cues[source] = cue
        was_visible = cue.id in self._row_by_id
        passes = predicate is None or predicate(cue)
        if was_visible and passes:
            row = self._row_by_id[cue.id]
            self._visible[row] = source
            index = self.index(row)
            self.dataChanged.emit(index, index)
            return
        self.beginResetModel()
        self._apply_visible(predicate)
        self.endResetModel()

    def cue_at(self, row: int) -> SubtitleCue | None:
        if 0 <= row < len(self._visible):
            return self._cues[self._visible[row]]
        return None

    def row_for_id(self, cue_id: str) -> int:
        return self._row_by_id.get(cue_id, -1)

    def rowCount(  # noqa: N802
        self,
        parent: QModelIndex | QPersistentModelIndex = _NO_PARENT,
    ) -> int:
        if parent.isValid():
            return 0
        return len(self._visible)

    def flags(self, index: QModelIndex | QPersistentModelIndex) -> Qt.ItemFlag:  # noqa: N802
        if not index.isValid():
            return Qt.ItemFlag.NoItemFlags
        return (
            Qt.ItemFlag.ItemIsEnabled
            | Qt.ItemFlag.ItemIsSelectable
            | Qt.ItemFlag.ItemIsEditable
        )

    def data(  # noqa: N802
        self,
        index: QModelIndex | QPersistentModelIndex,
        role: int = Qt.ItemDataRole.DisplayRole,
    ) -> str | SubtitleCue | None:
        cue = self.cue_at(index.row()) if index.isValid() else None
        if cue is None:
            return None
        if role == Qt.ItemDataRole.DisplayRole:
            return cue_text_for_table(cue.edited_text)
        if role == Qt.ItemDataRole.UserRole:
            return cue
        return None

    def _apply_visible(self, predicate: Callable[[SubtitleCue], bool] | None) -> None:
        self._source_by_id = {cue.id: i for i, cue in enumerate(self._cues)}
        if predicate is None:
            self._visible = list(range(len(self._cues)))
        else:
            self._visible = [i for i, cue in enumerate(self._cues) if predicate(cue)]
        self._row_by_id = {
            self._cues[source].id: row for row, source in enumerate(self._visible)
        }


class SubtitleCueRowDelegate(QStyledItemDelegate):
    def __init__(
        self,
        on_commit: Callable[[str, str], None],
        parent: QListView | None = None,
    ) -> None:
        super().__init__(parent)
        self._on_commit = on_commit

    def sizeHint(  # noqa: N802
        self,
        option: QStyleOptionViewItem,
        index: QModelIndex | QPersistentModelIndex,
    ) -> QSize:
        del index
        width = option.rect.width() if option.rect.width() > 0 else 160
        return QSize(width, ROW_HEIGHT)

    def paint(
        self,
        painter: QPainter,
        option: QStyleOptionViewItem,
        index: QModelIndex | QPersistentModelIndex,
    ) -> None:
        cue = index.data(Qt.ItemDataRole.UserRole)
        if not isinstance(cue, SubtitleCue):
            return
        selected = bool(option.state & QStyle.StateFlag.State_Selected)
        hovered = bool(option.state & QStyle.StateFlag.State_MouseOver)
        rect = option.rect
        base = option.palette.color(QPalette.ColorRole.Base)
        if selected:
            bg = QColor(base)
            bg = bg.lighter(128) if bg.lightness() < 128 else bg.darker(108)
        elif hovered:
            bg = QColor(base)
            bg = bg.lighter(118) if bg.lightness() < 128 else bg.darker(104)
        else:
            bg = base

        painter.save()
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
        painter.fillRect(rect, bg)
        show_actions = selected or hovered
        reset_rect, delete_rect = action_rects(rect)
        text_right = reset_rect.left() - 6 if show_actions else rect.right() - 8
        text_rect = QRect(rect.x() + 8, rect.y(), max(1, text_right - rect.x() - 8), rect.height())
        display = cue_text_for_table(cue.edited_text)
        painter.setFont(option.font)
        painter.setPen(
            option.palette.color(
                QPalette.ColorGroup.Active,
                QPalette.ColorRole.HighlightedText if selected else QPalette.ColorRole.Text,
            )
        )
        if any(token in display for token in _ESCAPE_MARKERS):
            _paint_marked_text(painter, option, text_rect, display, selected)
        else:
            elided = painter.fontMetrics().elidedText(
                display,
                Qt.TextElideMode.ElideRight,
                text_rect.width(),
            )
            painter.drawText(
                text_rect,
                int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter),
                elided,
            )
        if show_actions:
            reset_color = _RESET_COLOR if cue_can_reset_to_generated(cue) else _RESET_DISABLED
            painter.setPen(reset_color)
            painter.drawText(reset_rect, int(Qt.AlignmentFlag.AlignCenter), "↺")
            painter.setPen(_DELETE_COLOR)
            painter.drawText(delete_rect, int(Qt.AlignmentFlag.AlignCenter), "✕")
        painter.restore()

    def createEditor(  # noqa: N802
        self,
        parent: QWidget,
        option: QStyleOptionViewItem,
        index: QModelIndex | QPersistentModelIndex,
    ) -> QLineEdit:
        del option, index
        editor = QLineEdit(parent)
        editor.setFrame(False)
        return editor

    def setEditorData(  # noqa: N802
        self,
        editor: QWidget,
        index: QModelIndex | QPersistentModelIndex,
    ) -> None:
        if isinstance(editor, QLineEdit):
            editor.setText(str(index.data(Qt.ItemDataRole.DisplayRole) or ""))

    def setModelData(  # noqa: N802
        self,
        editor: QWidget,
        model: QAbstractItemModel,
        index: QModelIndex | QPersistentModelIndex,
    ) -> None:
        if not isinstance(editor, QLineEdit):
            return
        del model
        cue = index.data(Qt.ItemDataRole.UserRole)
        if not isinstance(cue, SubtitleCue):
            return
        text = cue_text_from_table(editor.text())
        if text != cue.edited_text:
            self._on_commit(cue.id, text)


def _paint_marked_text(
    painter: QPainter,
    option: QStyleOptionViewItem,
    text_rect: QRect,
    display: str,
    selected: bool,
) -> None:
    text_color = option.palette.color(
        QPalette.ColorGroup.Active,
        QPalette.ColorRole.HighlightedText if selected else QPalette.ColorRole.Text,
    )
    document = QTextDocument()
    document.setDefaultFont(option.font)
    document.setDefaultStyleSheet(f"body {{ color: {text_color.name()}; }}")
    document.setHtml(f"<body>{_display_html(display)}</body>")
    painter.save()
    painter.translate(text_rect.topLeft())
    document.setTextWidth(text_rect.width())
    clip = QRect(0, 0, text_rect.width(), text_rect.height())
    document.drawContents(painter, clip)
    painter.restore()


class SubtitleCueList(QListView):
    """Cue rows painted on demand. Action marks are drawn only for the active row."""

    current_cue_changed = Signal(str)
    cue_activated = Signal(str)
    reset_requested = Signal(str)
    delete_requested = Signal(str)
    text_edited = Signal(str, str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._model = SubtitleCueListModel(self)
        self.setModel(self._model)
        self.setItemDelegate(SubtitleCueRowDelegate(self.text_edited.emit, self))
        self.setUniformItemSizes(True)
        self.setMouseTracking(True)
        self.viewport().setAttribute(Qt.WidgetAttribute.WA_Hover, True)
        self.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.setEditTriggers(
            QAbstractItemView.EditTrigger.DoubleClicked
            | QAbstractItemView.EditTrigger.EditKeyPressed
        )
        self.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setFrameShape(QListView.Shape.NoFrame)
        self.setSpacing(0)
        self.setStyleSheet(
            "QListView { background: palette(base); border: none; outline: 0; }"
            "QListView::item { padding: 0px; margin: 0px; }"
            "QListView::item:selected { background: transparent; }"
            "QListView::item:hover { background: transparent; }"
        )
        self.verticalScrollBar().setSingleStep(ROW_HEIGHT)
        self.selectionModel().currentChanged.connect(self._on_current_changed)

    def set_cues(
        self,
        cues: list[SubtitleCue],
        *,
        predicate: Callable[[SubtitleCue], bool] | None = None,
    ) -> None:
        current_id = self.current_cue_id()
        selection = self.selectionModel()
        if selection is not None:
            selection.blockSignals(True)
        try:
            self._model.set_cues(cues, predicate=predicate)
            row = self._model.row_for_id(current_id) if current_id else -1
            if row >= 0:
                self.setCurrentIndex(self._model.index(row))
            else:
                self.setCurrentIndex(QModelIndex())
        finally:
            if selection is not None:
                selection.blockSignals(False)

    def refresh_cue(
        self,
        cue: SubtitleCue,
        *,
        predicate: Callable[[SubtitleCue], bool] | None = None,
    ) -> None:
        current_id = self.current_cue_id()
        selection = self.selectionModel()
        if selection is not None:
            selection.blockSignals(True)
        try:
            self._model.update_cue(cue, predicate=predicate)
            row = self.row_for_id(current_id) if current_id else -1
            if row >= 0:
                self.setCurrentIndex(self._model.index(row))
            else:
                self.setCurrentIndex(QModelIndex())
        finally:
            if selection is not None:
                selection.blockSignals(False)
        self.viewport().update()

    def row_count(self) -> int:
        return self._model.rowCount()

    def current_row(self) -> int:
        return self.currentIndex().row()

    def row_for_id(self, cue_id: str) -> int:
        return self._model.row_for_id(cue_id)

    def cue_at(self, row: int) -> SubtitleCue | None:
        return self._model.cue_at(row)

    def current_cue_id(self) -> str:
        cue = self.currentIndex().data(Qt.ItemDataRole.UserRole)
        return cue.id if isinstance(cue, SubtitleCue) else ""

    def select_row(self, row: int, *, scroll: bool = True) -> None:
        if row < 0:
            self.setCurrentIndex(QModelIndex())
            return
        index = self._model.index(row)
        self.setCurrentIndex(index)
        if scroll:
            self.scrollTo(index, QAbstractItemView.ScrollHint.PositionAtCenter)

    def mousePressEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        pos = event.position().toPoint()
        index = self.indexAt(pos)
        already = (
            event.button() == Qt.MouseButton.LeftButton
            and index.isValid()
            and index == self.currentIndex()
        )
        action = self._action_at(pos) if event.button() == Qt.MouseButton.LeftButton else None
        super().mousePressEvent(event)
        if action == "reset":
            cue = index.data(Qt.ItemDataRole.UserRole)
            if isinstance(cue, SubtitleCue) and cue_can_reset_to_generated(cue):
                self.reset_requested.emit(cue.id)
            return
        if action == "delete":
            cue = index.data(Qt.ItemDataRole.UserRole)
            if isinstance(cue, SubtitleCue):
                self.delete_requested.emit(cue.id)
            return
        if already:
            cue = index.data(Qt.ItemDataRole.UserRole)
            if isinstance(cue, SubtitleCue):
                self.cue_activated.emit(cue.id)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        action = self._action_at(event.position().toPoint())
        if action == "reset":
            self.setToolTip("Reset displayed line to auto-generated")
        elif action == "delete":
            self.setToolTip("Delete this cue")
        else:
            self.setToolTip("")
        super().mouseMoveEvent(event)

    def mouseDoubleClickEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if self._action_at(event.position().toPoint()):
            event.accept()
            return
        super().mouseDoubleClickEvent(event)

    def _action_at(self, pos: QPoint) -> str | None:
        index = self.indexAt(pos)
        if not index.isValid():
            return None
        reset_rect, delete_rect = action_rects(self.visualRect(index))
        if reset_rect.contains(pos):
            return "reset"
        if delete_rect.contains(pos):
            return "delete"
        return None

    def _on_current_changed(self, current: QModelIndex, _previous: QModelIndex) -> None:
        cue = current.data(Qt.ItemDataRole.UserRole) if current.isValid() else None
        self.current_cue_changed.emit(cue.id if isinstance(cue, SubtitleCue) else "")
        self.viewport().update()
