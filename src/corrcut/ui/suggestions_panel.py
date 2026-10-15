"""AI profanity suggestions review panel (Phase 5)."""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import (
    QAbstractItemModel,
    QEvent,
    QModelIndex,
    QPersistentModelIndex,
    QRect,
    QRectF,
    QSize,
    Qt,
    Signal,
)
from PySide6.QtGui import (
    QAction,
    QBrush,
    QColor,
    QCursor,
    QFont,
    QMouseEvent,
    QPainter,
    QPen,
)
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMenu,
    QPushButton,
    QSpinBox,
    QStyle,
    QStyledItemDelegate,
    QStyleOptionViewItem,
    QTableWidget,
    QTableWidgetItem,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from corrcut.ai.normalize import strip_display_punctuation
from corrcut.domain.ai_suggestion import AiSuggestion, DetectionSettings, SuggestionStatus
from corrcut.ui.buttons import use_pill_button
from corrcut.ui.job_progress_banner import JobProgressBanner
from corrcut.ui.table_helpers import (
    configure_panel_table,
    scroll_row_to_center,
    select_playhead_row,
    timed_item_for_playhead,
)

_ACTION_SIZE = 22
_ACTION_GAP = 4
_ACCEPT_MARK = "✓"
_REJECT_MARK = "✕"


def action_button_rects(cell: QRect) -> tuple[QRect, QRect]:
    """Accept and reject squares centered in a suggestion actions cell."""
    size = min(_ACTION_SIZE, max(12, cell.height() - 4))
    total = size * 2 + _ACTION_GAP
    left = cell.left() + max(0, (cell.width() - total) // 2)
    top = cell.top() + max(0, (cell.height() - size) // 2)
    accept = QRect(left, top, size, size)
    reject = QRect(left + size + _ACTION_GAP, top, size, size)
    return accept, reject


def _paint_action_button(
    painter: QPainter,
    rect: QRect,
    mark: str,
    *,
    active: bool,
    hover: bool,
    active_fill: QColor,
    hover_fill: QColor,
    hover_text: QColor,
) -> None:
    painter.save()
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    if active:
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(active_fill)
        painter.drawRoundedRect(QRectF(rect), 4, 4)
        text = QColor("#ffffff")
    elif hover:
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(hover_fill)
        painter.drawRoundedRect(QRectF(rect), 4, 4)
        text = hover_text
    else:
        text = QColor("#8a8a8a")
    font = QFont(painter.font())
    font.setPixelSize(14)
    font.setBold(True)
    painter.setFont(font)
    painter.setPen(QPen(text))
    painter.drawText(rect, int(Qt.AlignmentFlag.AlignCenter), mark)
    painter.restore()


class SuggestionActionDelegate(QStyledItemDelegate):
    """Paint accept and reject marks in the cell.

    Real buttons become native windows on a scaled monitor and overlap the last row.
    """

    def __init__(
        self,
        status_role: int,
        on_clicked: Callable[[str, str], None],
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._status_role = status_role
        self._on_clicked = on_clicked

    def paint(
        self,
        painter: QPainter,
        option: QStyleOptionViewItem,
        index: QModelIndex | QPersistentModelIndex,
    ) -> None:
        status_name = index.data(self._status_role) or SuggestionStatus.PENDING.value
        try:
            status = SuggestionStatus(str(status_name))
        except ValueError:
            status = SuggestionStatus.PENDING
        view_option = QStyleOptionViewItem(option)
        view_option.state = view_option.state & ~QStyle.StateFlag.State_HasFocus
        super().paint(painter, view_option, index)
        accept_rect, reject_rect = action_button_rects(option.rect)
        hovered = self._hovered_side(option, accept_rect, reject_rect)
        _paint_action_button(
            painter,
            accept_rect,
            _ACCEPT_MARK,
            active=status == SuggestionStatus.ACCEPTED,
            hover=hovered == "accept",
            active_fill=QColor("#3d8b3d"),
            hover_fill=QColor(80, 180, 80, 46),
            hover_text=QColor("#5cb85c"),
        )
        _paint_action_button(
            painter,
            reject_rect,
            _REJECT_MARK,
            active=status == SuggestionStatus.REJECTED,
            hover=hovered == "reject",
            active_fill=QColor("#b33a3a"),
            hover_fill=QColor(200, 60, 60, 46),
            hover_text=QColor("#d64545"),
        )

    def sizeHint(
        self, option: QStyleOptionViewItem, index: QModelIndex | QPersistentModelIndex
    ) -> QSize:
        del option, index
        return QSize(_ACTION_SIZE * 2 + _ACTION_GAP + 8, _ACTION_SIZE + 6)

    def editorEvent(
        self,
        event: QEvent,
        model: QAbstractItemModel,
        option: QStyleOptionViewItem,
        index: QModelIndex | QPersistentModelIndex,
    ) -> bool:
        del model
        if event.type() != QEvent.Type.MouseButtonRelease or not isinstance(event, QMouseEvent):
            return False
        if event.button() != Qt.MouseButton.LeftButton:
            return False
        suggestion_id = index.data(Qt.ItemDataRole.UserRole)
        if not suggestion_id:
            return False
        pos = event.position().toPoint()
        accept_rect, reject_rect = action_button_rects(option.rect)
        if accept_rect.contains(pos):
            self._on_clicked(str(suggestion_id), "accept")
            return True
        if reject_rect.contains(pos):
            self._on_clicked(str(suggestion_id), "reject")
            return True
        return False

    def _hovered_side(
        self,
        option: QStyleOptionViewItem,
        accept_rect: QRect,
        reject_rect: QRect,
    ) -> str | None:
        if not (option.state & QStyle.StateFlag.State_MouseOver):
            return None
        widget = option.widget
        if widget is None:
            return None
        pos = widget.mapFromGlobal(QCursor.pos())
        if accept_rect.contains(pos):
            return "accept"
        if reject_rect.contains(pos):
            return "reject"
        return None


class SuggestionsPanel(QWidget):
    generate_requested = Signal(object)  # DetectionSettings
    seek_requested = Signal(float)  # start time in seconds
    accept_requested = Signal(str)
    reject_requested = Signal(str)
    pending_requested = Signal(str)
    job_cancel_requested = Signal()

    _COL_ACTIONS = 0
    _COL_TIME = 1
    _COL_WORD = 2
    _COL_CAT = 3
    _COL_SOURCE = 4
    _ROLE_START_SECONDS = int(Qt.ItemDataRole.UserRole) + 1
    _ROLE_STATUS = int(Qt.ItemDataRole.UserRole) + 2

    def __init__(self) -> None:
        super().__init__()
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("<b>AI suggestions</b>"))

        self._model_combo = QComboBox()
        self._model_combo.addItems(["tiny", "base", "small"])
        self._model_combo.setCurrentText("base")

        self._pre_pad = QSpinBox()
        self._pre_pad.setRange(0, 2000)
        self._pre_pad.setValue(150)
        self._pre_pad.setSuffix(" ms")

        self._post_pad = QSpinBox()
        self._post_pad.setRange(0, 2000)
        self._post_pad.setValue(150)
        self._post_pad.setSuffix(" ms")

        self._use_subs = QCheckBox("Use text subtitles")
        self._use_subs.setChecked(True)
        self._use_subs.setToolTip(
            "Match profanity in embedded SRT/ASS/SubRip tracks or OCR-converted bitmap cues; "
            "merged with Whisper when both are available, so the same word does not appear twice"
        )

        btn_row = QHBoxLayout()
        self._generate_btn = QPushButton("Generate suggestions…")
        self._generate_btn.setDefault(True)
        use_pill_button(self._generate_btn, primary=True)
        self._generate_btn.clicked.connect(self._emit_generate)
        btn_row.addWidget(self._generate_btn)

        self._settings_btn = QToolButton()
        self._settings_btn.setText("Settings…")
        use_pill_button(self._settings_btn)
        self._settings_btn.setToolTip("Whisper detection settings")
        self._settings_btn.clicked.connect(self._open_settings_dialog)
        btn_row.addWidget(self._settings_btn)
        btn_row.addStretch()

        self._filter_btn = QToolButton()
        self._filter_btn.setText("⋮")
        self._filter_btn.setToolTip("Filter suggestions by status")
        self._filter_btn.setAutoRaise(True)
        self._filter_btn.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        filter_menu = QMenu(self)
        self._filter_pending = filter_menu.addAction("Pending")
        self._filter_pending.setCheckable(True)
        self._filter_pending.setChecked(True)
        self._filter_accepted = filter_menu.addAction("Accepted")
        self._filter_accepted.setCheckable(True)
        self._filter_accepted.setChecked(True)
        self._filter_rejected = filter_menu.addAction("Rejected")
        self._filter_rejected.setCheckable(True)
        self._filter_rejected.setChecked(True)
        for action in (self._filter_pending, self._filter_accepted, self._filter_rejected):
            action.triggered.connect(self._on_filter_changed)
        self._filter_btn.setMenu(filter_menu)
        btn_row.addWidget(self._filter_btn)
        layout.addLayout(btn_row)

        self._job_banner = JobProgressBanner()
        self._job_banner.cancel_requested.connect(self.job_cancel_requested.emit)
        layout.addWidget(self._job_banner)

        self._all_suggestions: list[AiSuggestion] = []

        self._table = QTableWidget(0, 5)
        self._table.setHorizontalHeaderLabels(["", "Time", "Word", "Category", "Source"])
        header = self._table.horizontalHeader()
        header.setStretchLastSection(False)
        header.setSectionResizeMode(self._COL_ACTIONS, QHeaderView.ResizeMode.Fixed)
        header.resizeSection(self._COL_ACTIONS, 56)
        header.setSectionResizeMode(self._COL_TIME, QHeaderView.ResizeMode.Fixed)
        header.resizeSection(self._COL_TIME, 112)
        header.setSectionResizeMode(self._COL_WORD, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(self._COL_CAT, QHeaderView.ResizeMode.Fixed)
        header.resizeSection(self._COL_CAT, 88)
        header.setSectionResizeMode(self._COL_SOURCE, QHeaderView.ResizeMode.Fixed)
        header.resizeSection(self._COL_SOURCE, 72)
        configure_panel_table(self._table)
        self._table.setMouseTracking(True)
        self._table.viewport().setMouseTracking(True)
        self._action_delegate = SuggestionActionDelegate(
            self._ROLE_STATUS,
            self._on_action_clicked,
            self._table,
        )
        self._table.setItemDelegateForColumn(self._COL_ACTIONS, self._action_delegate)
        self._table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self._table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self._table.itemSelectionChanged.connect(self._on_table_selection)
        self._table.cellClicked.connect(self._on_cell_clicked)
        self._loading_table = False
        self._syncing_playhead = False
        layout.addWidget(self._table)

        self._summary = QLabel("No suggestions yet.")
        layout.addWidget(self._summary)

        self._settings_dialog = self._build_settings_dialog()

    def _build_settings_dialog(self) -> QDialog:
        dialog = QDialog(self)
        dialog.setWindowTitle("Detection settings")
        layout = QVBoxLayout(dialog)
        form = QFormLayout()
        form.addRow("Whisper model", self._model_combo)
        form.addRow("Pre-padding", self._pre_pad)
        form.addRow("Post-padding", self._post_pad)
        form.addRow(self._use_subs)
        layout.addLayout(form)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        return dialog

    def _open_settings_dialog(self) -> None:
        self._settings_dialog.exec()

    def detection_settings(self) -> DetectionSettings:
        return DetectionSettings(
            whisper_model=self._model_combo.currentText(),
            pre_padding_ms=int(self._pre_pad.value()),
            post_padding_ms=int(self._post_pad.value()),
            use_subtitles=self._use_subs.isChecked(),
        )

    @staticmethod
    def _source_label(detection_source: str) -> str:
        labels = {
            "whisper": "Audio",
            "subtitle": "Subs",
            "both": "Both",
        }
        return labels.get(detection_source, detection_source)

    @staticmethod
    def _status_row_brush(status: SuggestionStatus) -> QBrush:
        colors = {
            SuggestionStatus.PENDING: QColor(90, 75, 35),
            SuggestionStatus.ACCEPTED: QColor(45, 90, 55),
            SuggestionStatus.REJECTED: QColor(85, 45, 45),
        }
        return QBrush(colors[status])

    def _make_cell(self, text: str, status: SuggestionStatus) -> QTableWidgetItem:
        item = QTableWidgetItem(text)
        item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
        item.setBackground(self._status_row_brush(status))
        return item

    def _on_action_clicked(self, suggestion_id: str, kind: str) -> None:
        status = SuggestionStatus.PENDING
        for suggestion in self._all_suggestions:
            if suggestion.id == suggestion_id:
                status = suggestion.status
                break
        if kind == "accept":
            if status == SuggestionStatus.ACCEPTED:
                self.pending_requested.emit(suggestion_id)
            else:
                self.accept_requested.emit(suggestion_id)
            return
        if status == SuggestionStatus.REJECTED:
            self.pending_requested.emit(suggestion_id)
        else:
            self.reject_requested.emit(suggestion_id)

    def load_suggestions(self, suggestions: list[AiSuggestion]) -> None:
        self._all_suggestions = list(suggestions)
        self._reload_table()

    def _filtered_suggestions(self) -> list[AiSuggestion]:
        visible: list[AiSuggestion] = []
        for suggestion in self._all_suggestions:
            if (
                suggestion.status == SuggestionStatus.PENDING
                and self._filter_pending.isChecked()
                or suggestion.status == SuggestionStatus.ACCEPTED
                and self._filter_accepted.isChecked()
                or suggestion.status == SuggestionStatus.REJECTED
                and self._filter_rejected.isChecked()
            ):
                visible.append(suggestion)
        return visible

    def _on_filter_changed(self, _checked: bool = False) -> None:
        filters = (self._filter_pending, self._filter_accepted, self._filter_rejected)
        if not any(action.isChecked() for action in filters):
            action = self.sender()
            if isinstance(action, QAction):
                action.setChecked(True)
            return
        self._reload_table()

    def _reload_table(self) -> None:
        suggestions = self._filtered_suggestions()
        scroll_bar = self._table.verticalScrollBar()
        scroll_pos = scroll_bar.value()
        self._loading_table = True
        self._table.setRowCount(0)
        for suggestion in suggestions:
            row = self._table.rowCount()
            self._table.insertRow(row)
            start_s = float(suggestion.time_range.start.seconds)
            end_s = float(suggestion.time_range.end.seconds)
            status = suggestion.status

            actions_item = QTableWidgetItem()
            actions_item.setFlags(Qt.ItemFlag.ItemIsEnabled)
            actions_item.setBackground(self._status_row_brush(status))
            actions_item.setData(Qt.ItemDataRole.UserRole, suggestion.id)
            actions_item.setData(self._ROLE_STATUS, status.value)
            actions_item.setToolTip("Accept (✓) or reject (✕). Click again to mark pending.")
            self._table.setItem(row, self._COL_ACTIONS, actions_item)

            time_item = self._make_cell(f"{start_s:.2f}–{end_s:.2f}s", status)
            time_item.setData(Qt.ItemDataRole.UserRole, suggestion.id)
            time_item.setData(self._ROLE_START_SECONDS, start_s)
            self._table.setItem(row, self._COL_TIME, time_item)

            word = strip_display_punctuation(suggestion.matched_word) or suggestion.matched_word
            for col, text in (
                (self._COL_WORD, word),
                (self._COL_CAT, suggestion.category or "—"),
                (self._COL_SOURCE, self._source_label(suggestion.detection_source)),
            ):
                self._table.setItem(row, col, self._make_cell(text, status))

        total = len(self._all_suggestions)
        pending = sum(1 for s in self._all_suggestions if s.status == SuggestionStatus.PENDING)
        shown = len(suggestions)
        if shown == total:
            self._summary.setText(f"{total} suggestion(s) — {pending} pending review")
        else:
            self._summary.setText(f"{shown} of {total} shown — {pending} pending review")
        self._loading_table = False
        scroll_bar.setValue(min(scroll_pos, scroll_bar.maximum()))

    def _row_for_suggestion_id(self, suggestion_id: str) -> int:
        for row in range(self._table.rowCount()):
            item = self._table.item(row, self._COL_TIME)
            if item is not None and item.data(Qt.ItemDataRole.UserRole) == suggestion_id:
                return row
        return -1

    def follow_playhead(self, seconds: float) -> None:
        """Select the visible suggestion at ``seconds`` without seeking."""
        suggestions = self._filtered_suggestions()
        if not suggestions:
            return
        suggestion = timed_item_for_playhead(
            suggestions,
            seconds,
            start_seconds=lambda s: float(s.time_range.start.seconds),
            end_seconds=lambda s: float(s.time_range.end.seconds),
        )
        if suggestion is None:
            return
        row = self._row_for_suggestion_id(suggestion.id)
        if row < 0:
            return
        if self._table.currentRow() == row:
            scroll_row_to_center(self._table, row)
            return
        self._syncing_playhead = True
        try:
            select_playhead_row(self._table, row, column=self._COL_TIME)
        finally:
            self._syncing_playhead = False

    def _start_seconds_for_row(self, row: int) -> float | None:
        item = self._table.item(row, self._COL_TIME)
        if item is None:
            return None
        value = item.data(self._ROLE_START_SECONDS)
        if value is None:
            return None
        return float(value)

    def _emit_seek_for_row(self, row: int) -> None:
        start_s = self._start_seconds_for_row(row)
        if start_s is not None:
            self.seek_requested.emit(start_s)

    def _on_table_selection(self) -> None:
        if self._loading_table or self._syncing_playhead:
            return
        rows = self._table.selectionModel().selectedRows()
        if not rows:
            return
        self._emit_seek_for_row(rows[0].row())

    def _on_cell_clicked(self, row: int, column: int) -> None:
        """Seek even when the row was already selected (Qt skips selectionChanged)."""
        if self._loading_table or column == self._COL_ACTIONS:
            return
        self._emit_seek_for_row(row)

    def show_job_banner(self, heading: str) -> None:
        self._job_banner.start(heading)

    def set_job_progress(self, percent: int, message: str) -> None:
        self._job_banner.set_progress(percent, message)

    def note_job_detail(self, line: str) -> None:
        self._job_banner.set_detail(line)

    def mark_job_cancelling(self) -> None:
        self._job_banner.mark_cancelling()

    def hide_job_banner(self) -> None:
        self._job_banner.hide_job()

    def set_busy(self, busy: bool) -> None:
        """Block a second detection run. Review, filter, and accept stay available."""
        self._generate_btn.setEnabled(not busy)

    def _emit_generate(self) -> None:
        self.generate_requested.emit(self.detection_settings())
