"""Resolve-style painted list of censor operations (no per-row cell widgets)."""

from __future__ import annotations

from PySide6.QtCore import QAbstractListModel, QModelIndex, QRect, QSize, Qt, Signal
from PySide6.QtGui import QBrush, QColor, QFont, QKeyEvent, QMouseEvent, QPainter, QPalette, QPen
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QListView,
    QStyle,
    QStyledItemDelegate,
    QStyleOptionViewItem,
)

from corrcut.domain.corr_categories import CATEGORY_OPTIONS, resolve_categories
from corrcut.domain.operation import Operation, OperationType
from corrcut.ui.icons import app_icon
from corrcut.ui.theme import contrast_text_on, muted_text_on
from corrcut.ui.timeline.region_item import region_color

TYPE_LABELS = {
    OperationType.MUTE: "Mute",
    OperationType.VOLUME: "Volume",
    OperationType.BEEP: "Beep",
    OperationType.SKIP: "Skip",
    OperationType.BLANK: "Blank",
    OperationType.COVER: "Cover",
    OperationType.BLUR: "Blur",
    OperationType.PIXELATE: "Pixelate",
    OperationType.ZOOM: "Zoom",
    OperationType.CROP: "Crop",
}

AUDIO_EDITABLE_TYPES = (
    OperationType.MUTE,
    OperationType.VOLUME,
    OperationType.BEEP,
)
VISUAL_EDITABLE_TYPES = (
    OperationType.COVER,
    OperationType.BLUR,
    OperationType.PIXELATE,
    OperationType.ZOOM,
    OperationType.CROP,
    OperationType.BLANK,
    OperationType.SKIP,
)
EDITABLE_TYPES = AUDIO_EDITABLE_TYPES + VISUAL_EDITABLE_TYPES


def populate_operation_type_combo(combo: QComboBox) -> None:
    """Fill the edit-type menu with Audio / Visual sections and matching icons."""
    combo.clear()
    combo.setIconSize(QSize(16, 16))
    _add_type_section(combo, "Audio")
    audio_icon = app_icon("mdi6.volume-high")
    for op_type in AUDIO_EDITABLE_TYPES:
        combo.addItem(audio_icon, TYPE_LABELS[op_type], op_type.value)
    combo.insertSeparator(combo.count())
    _add_type_section(combo, "Visual")
    visual_icon = app_icon("mdi6.monitor")
    for op_type in VISUAL_EDITABLE_TYPES:
        combo.addItem(visual_icon, TYPE_LABELS[op_type], op_type.value)


def _add_type_section(combo: QComboBox, title: str) -> None:
    combo.addItem(title)
    model = combo.model()
    item = model.item(combo.count() - 1)  # type: ignore[attr-defined]
    if item is None:
        return
    item.setEnabled(False)
    font = item.font()
    font.setBold(True)
    item.setFont(font)
    item.setForeground(QBrush(QColor("#9a9aa2")))

ROW_HEIGHT = 44
_ACCENT_W = 4
_CATEGORY_LABELS = {option.id: option.combo_label() for option in CATEGORY_OPTIONS}


def accent_color(op_type: OperationType) -> QColor:
    """Opaque stripe color matching the timeline region."""
    color = QColor(region_color(op_type))
    color.setAlpha(255)
    if color.lightness() < 40:
        return QColor(150, 150, 160)
    return color


def format_span(seconds: float) -> str:
    seconds = max(0.0, float(seconds))
    total_ms = int(round(seconds * 1000.0))
    ms = total_ms % 1000
    total_s = total_ms // 1000
    s = total_s % 60
    total_m = total_s // 60
    m = total_m % 60
    h = total_m // 60
    if h:
        return f"{h}:{m:02d}:{s:02d}.{ms:03d}"
    return f"{m}:{s:02d}.{ms:03d}"


_SUBTITLE_SEP = "  ·  "


def category_subtitle_for(op: Operation) -> str:
    labels = [
        _CATEGORY_LABELS.get(cat, cat) for cat in resolve_categories(op.categories)
    ]
    return ", ".join(label for label in labels if label)


def subtitle_for(op: Operation) -> str:
    description = (op.description or "").strip()
    categories = category_subtitle_for(op)
    if description and categories:
        return f"{description}{_SUBTITLE_SEP}{categories}"
    return description or categories


def subtitle_slot_widths(
    metrics, description: str, categories: str, width: int
) -> tuple[int, int]:
    """Pixel widths for independently elided description then categories."""
    width = max(0, int(width))
    if not description:
        return 0, width
    if not categories:
        return width, 0
    sep_w = metrics.horizontalAdvance(_SUBTITLE_SEP)
    budget = max(0, width - sep_w)
    desc_nat = metrics.horizontalAdvance(description)
    cat_nat = metrics.horizontalAdvance(categories)
    if desc_nat + cat_nat <= budget:
        return desc_nat, cat_nat
    min_cat = min(cat_nat, max(metrics.horizontalAdvance("…"), budget // 3))
    desc_w = min(desc_nat, budget - min_cat)
    return desc_w, budget - desc_w


def _draw_subtitle_line(
    painter: QPainter, rect: QRect, description: str, categories: str
) -> None:
    metrics = painter.fontMetrics()
    align = int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
    if not description or not categories:
        text = description or categories
        elided = metrics.elidedText(text, Qt.TextElideMode.ElideRight, rect.width())
        painter.drawText(rect, align, elided)
        return
    desc_w, cat_w = subtitle_slot_widths(metrics, description, categories, rect.width())
    desc = metrics.elidedText(description, Qt.TextElideMode.ElideRight, desc_w)
    cat = metrics.elidedText(categories, Qt.TextElideMode.ElideRight, cat_w)
    x = rect.x()
    desc_width = metrics.horizontalAdvance(desc)
    painter.drawText(QRect(x, rect.y(), desc_width, rect.height()), align, desc)
    x += desc_width
    sep_width = metrics.horizontalAdvance(_SUBTITLE_SEP)
    painter.drawText(QRect(x, rect.y(), sep_width, rect.height()), align, _SUBTITLE_SEP)
    x += sep_width
    remaining = max(1, rect.right() - x + 1)
    painter.drawText(QRect(x, rect.y(), remaining, rect.height()), align, cat)


class OperationsListModel(QAbstractListModel):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._ops: list[Operation] = []

    def set_operations(self, operations: list[Operation]) -> None:
        self.beginResetModel()
        self._ops = sorted(operations, key=lambda op: op.time_range.start.seconds)
        self.endResetModel()

    def update_operation(self, op: Operation) -> int:
        for row, existing in enumerate(self._ops):
            if existing.id == op.id:
                self._ops[row] = op
                index = self.index(row)
                self.dataChanged.emit(index, index)
                return row
        return -1

    def operation_at(self, row: int) -> Operation | None:
        if 0 <= row < len(self._ops):
            return self._ops[row]
        return None

    def row_for_id(self, op_id: str) -> int:
        for row, op in enumerate(self._ops):
            if op.id == op_id:
                return row
        return -1

    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:  # noqa: N802
        if parent.isValid():
            return 0
        return len(self._ops)

    def data(self, index: QModelIndex, role: int = Qt.ItemDataRole.DisplayRole):  # noqa: N802
        op = self.operation_at(index.row()) if index.isValid() else None
        if op is None:
            return None
        if role == Qt.ItemDataRole.DisplayRole:
            return TYPE_LABELS.get(op.type, op.type.value)
        if role == Qt.ItemDataRole.UserRole:
            return op
        return None


class OperationsRowDelegate(QStyledItemDelegate):
    def sizeHint(self, option: QStyleOptionViewItem, index: QModelIndex) -> QSize:  # noqa: N802
        return QSize(option.rect.width() if option.rect.width() > 0 else 160, ROW_HEIGHT)

    def paint(
        self,
        painter: QPainter,
        option: QStyleOptionViewItem,
        index: QModelIndex,
    ) -> None:
        op = index.data(Qt.ItemDataRole.UserRole)
        if not isinstance(op, Operation):
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
        accent = accent_color(op.type)
        if not op.enabled:
            accent.setAlpha(90)
        painter.fillRect(QRect(rect.x(), rect.y(), _ACCENT_W, rect.height()), accent)

        line = QColor(255, 255, 255, 16) if base.lightness() < 128 else QColor(0, 0, 0, 22)
        painter.setPen(QPen(line, 1))
        painter.drawLine(rect.bottomLeft(), rect.bottomRight())

        type_font = QFont(option.font)
        type_font.setPixelSize(12)
        type_font.setWeight(QFont.Weight.DemiBold)
        time_font = QFont(option.font)
        time_font.setPixelSize(11)
        sub_font = QFont(option.font)
        sub_font.setPixelSize(11)

        text_color = contrast_text_on(bg, option.palette.color(QPalette.ColorRole.Text))
        muted = muted_text_on(bg, text_color)
        if not op.enabled:
            text_color = muted
        start = format_span(float(op.time_range.start.seconds))
        end = format_span(float(op.time_range.end.seconds))
        time_text = f"{start}  –  {end}"
        type_text = TYPE_LABELS.get(op.type, op.type.value)
        if not op.enabled:
            type_text = f"{type_text}  ·  off"
        description = (op.description or "").strip()
        categories = category_subtitle_for(op)

        text_right = rect.right() - 10
        text_left = rect.x() + _ACCENT_W + 10
        top = QRect(text_left, rect.y() + 4, max(1, text_right - text_left), 18)
        bottom = QRect(text_left, rect.y() + 22, max(1, text_right - text_left), 16)

        painter.setFont(type_font)
        painter.setPen(text_color)
        painter.drawText(top, int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter), type_text)
        painter.setFont(time_font)
        painter.setPen(muted)
        painter.drawText(top, int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter), time_text)
        if description or categories:
            painter.setFont(sub_font)
            painter.setPen(muted)
            _draw_subtitle_line(painter, bottom, description, categories)

        painter.restore()


class OperationsListView(QListView):
    current_op_changed = Signal(str)
    row_clicked = Signal(str)
    delete_requested = Signal(str)
    toggle_enabled_requested = Signal(str)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._model = OperationsListModel(self)
        self.setModel(self._model)
        self.setItemDelegate(OperationsRowDelegate(self))
        self.setUniformItemSizes(True)
        self.setMouseTracking(True)
        self.viewport().setAttribute(Qt.WidgetAttribute.WA_Hover, True)
        self.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.setDragEnabled(False)
        self.setDragDropMode(QAbstractItemView.DragDropMode.NoDragDrop)
        self.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setFrameShape(QListView.Shape.NoFrame)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setSpacing(0)
        self.setStyleSheet(
            "QListView { background: palette(base); border: none; outline: 0; }"
            "QListView::item { padding: 0px; margin: 0px; }"
            "QListView::item:selected { background: transparent; }"
            "QListView::item:hover { background: transparent; }"
        )
        self.verticalScrollBar().setSingleStep(ROW_HEIGHT)
        self.selectionModel().currentChanged.connect(self._on_current_changed)

    @property
    def list_model(self) -> OperationsListModel:
        return self._model

    def set_operations(self, operations: list[Operation]) -> None:
        self._model.set_operations(operations)

    def refresh_operation(self, op: Operation) -> None:
        self._model.update_operation(op)

    def row_for_id(self, op_id: str) -> int:
        return self._model.row_for_id(op_id)

    def operation_at(self, row: int) -> Operation | None:
        return self._model.operation_at(row)

    def current_row(self) -> int:
        return self.currentIndex().row()

    def select_row(self, row: int, *, scroll: bool = False) -> None:
        if row < 0:
            self.setCurrentIndex(QModelIndex())
            return
        index = self._model.index(row)
        self.setCurrentIndex(index)
        if scroll:
            # Reveal the row the way a system list does: leave it where it is
            # when it is already on screen, and scroll only far enough to show
            # it when it is not. Centering jumps the row under the cursor.
            self.scrollTo(index, QAbstractItemView.ScrollHint.EnsureVisible)

    def mousePressEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        pos = event.position().toPoint()
        index = self.indexAt(pos)
        already = (
            event.button() == Qt.MouseButton.LeftButton
            and index.isValid()
            and index == self.currentIndex()
        )
        super().mousePressEvent(event)
        if already:
            op = index.data(Qt.ItemDataRole.UserRole)
            if isinstance(op, Operation):
                self.row_clicked.emit(op.id)

    def keyPressEvent(self, event: QKeyEvent) -> None:  # noqa: N802
        if event.key() in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace):
            op = self.currentIndex().data(Qt.ItemDataRole.UserRole)
            if isinstance(op, Operation):
                self.delete_requested.emit(op.id)
                event.accept()
                return
        if event.key() == Qt.Key.Key_D and not event.modifiers():
            op = self.currentIndex().data(Qt.ItemDataRole.UserRole)
            if isinstance(op, Operation):
                self.toggle_enabled_requested.emit(op.id)
                event.accept()
                return
        super().keyPressEvent(event)

    def _on_current_changed(self, current: QModelIndex, _previous: QModelIndex) -> None:
        op = current.data(Qt.ItemDataRole.UserRole) if current.isValid() else None
        self.current_op_changed.emit(op.id if isinstance(op, Operation) else "")
        self.viewport().update()
