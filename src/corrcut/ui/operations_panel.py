"""List and edit censor operations."""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QDoubleSpinBox,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QScrollArea,
    QSizePolicy,
    QSplitter,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from corrcut.domain.audio_language import edit_language_label
from corrcut.domain.channel_layout import channels_for_layout
from corrcut.domain.channel_scope import (
    checks_from_scope_channels,
    editable_channel_names,
)
from corrcut.domain.corr_categories import (
    OTHER_ID,
    resolve_categories,
)
from corrcut.domain.dialogue_target import DialogueTargetStrategy
from corrcut.domain.operation import (
    SEVERITY_LEVELS,
    Operation,
    OperationType,
    is_audio_operation,
    normalize_operation_type,
)
from corrcut.ui.beep_tone_combo import populate_beep_tone_combo
from corrcut.ui.category_chips import CategoryChipsPicker
from corrcut.ui.channel_room import ChannelRoomWidget, ChannelSpeakerHandle
from corrcut.ui.operations_list import (
    OperationsListView,
    populate_operation_type_combo,
)
from corrcut.ui.table_helpers import timed_item_for_playhead
from corrcut.ui.theme import apply_muted_text
from corrcut.ui.toggle_switch import ToggleSwitch

_INSPECTOR_MIN_WIDTH = 240
_LIST_MIN_WIDTH = 200
_FIELD_HEIGHT = 30


def populate_severity_combo(combo: QComboBox, value: int | None) -> None:
    combo.blockSignals(True)
    combo.clear()
    combo.addItem("(Optional)", None)
    for label, number in SEVERITY_LEVELS:
        combo.addItem(label, number)
    if value is None:
        combo.setCurrentIndex(0)
    else:
        index = combo.findData(value)
        if index < 0:
            combo.insertItem(1, str(value), value)
            combo.setCurrentIndex(1)
        else:
            combo.setCurrentIndex(index)
    combo.blockSignals(False)


class OperationsPanel(QWidget):
    delete_requested = Signal(str)
    selection_changed = Signal(str)
    censor_params_changed = Signal(str, OperationType, object)
    channels_changed = Signal(str, list)
    enabled_changed = Signal(str, bool)
    operation_meta_changed = Signal(str)

    def __init__(self) -> None:
        super().__init__()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(6)

        heading = QHBoxLayout()
        heading.setContentsMargins(0, 0, 0, 2)
        heading.setSpacing(8)
        title = QLabel("Edits")
        title.setStyleSheet("font-weight: 600; font-size: 13px;")
        title.setAlignment(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft)
        self._count_label = QLabel()
        self._count_label.setStyleSheet("color: #888; font-size: 11px;")
        self._count_label.setAlignment(
            Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft
        )
        heading.addWidget(title)
        heading.addWidget(self._count_label)
        heading.addStretch(1)

        self._list = OperationsListView()
        self._list.setMinimumWidth(_LIST_MIN_WIDTH)
        self._list.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

        self._editor = QWidget()
        editor_layout = QVBoxLayout(self._editor)
        editor_layout.setContentsMargins(0, 0, 0, 0)
        editor_layout.setSpacing(0)
        form_host = QWidget()
        form_host.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Maximum)
        self._form = QFormLayout(form_host)
        self._form.setContentsMargins(4, 10, 8, 8)
        self._form.setVerticalSpacing(12)
        self._form.setHorizontalSpacing(12)
        self._form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        self._form.setFormAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
        self._form.setLabelAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
        self._form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
        self._type_combo = QComboBox()
        populate_operation_type_combo(self._type_combo)
        self._categories = CategoryChipsPicker()
        self._categories.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Maximum
        )
        self._description_edit = QLineEdit()
        self._description_edit.setPlaceholderText("(Optional)")
        self._severity_combo = QComboBox()
        self._severity_combo.setToolTip(
            "Optional corr.json severity (1–5): Very mild, Mild, Moderate, "
            "Severe, Extreme. Unset is omitted from the sidecar."
        )
        populate_severity_combo(self._severity_combo, None)
        self._beep_combo = QComboBox()
        self._beep_combo.setToolTip("Beep tone written as corr.json frequency")
        populate_beep_tone_combo(self._beep_combo, 1000.0)
        self._gain_spin = QDoubleSpinBox()
        self._gain_spin.setRange(0.0, 1.0)
        self._gain_spin.setSingleStep(0.05)
        self._gain_spin.setDecimals(2)
        self._radius_spin = QDoubleSpinBox()
        self._radius_spin.setRange(0.5, 500.0)
        self._size_spin = QDoubleSpinBox()
        self._size_spin.setRange(2.0, 128.0)
        self._scale_spin = QDoubleSpinBox()
        self._scale_spin.setRange(1.01, 10.0)
        self._scale_spin.setSingleStep(0.1)
        self._channels_widget = ChannelRoomWidget()
        self._channels_widget.setSizePolicy(
            QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Maximum
        )
        self._channels_widget.changed.connect(self._emit_channels_changed)
        self._channel_note = QLabel(
            'Click a speaker to turn it on or off. '
            '<a href="toggle-all" style="color:#888;text-decoration:underline;">'
            "Toggle all</a>"
        )
        self._channel_note.setTextFormat(Qt.TextFormat.RichText)
        self._channel_note.setTextInteractionFlags(
            Qt.TextInteractionFlag.LinksAccessibleByMouse
        )
        self._channel_note.setWordWrap(True)
        self._channel_note.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        self._channel_note.setStyleSheet("color: #888;")
        self._channel_note.setToolTip("Turn every speaker on or off")
        self._channel_note.linkActivated.connect(self._toggle_all_speakers)
        self._channels_block = QWidget()
        self._channels_block.setSizePolicy(
            QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Maximum
        )
        channels_col = QVBoxLayout(self._channels_block)
        channels_col.setContentsMargins(0, 0, 0, 0)
        channels_col.setSpacing(6)
        channels_col.addWidget(self._channels_widget)
        channels_col.addWidget(self._channel_note)
        self._enabled_check = ToggleSwitch()
        self._enabled_check.setObjectName("enabledToggle")
        self._enabled_check.setToolTip("Shortcut: D")
        # Type-specific controls first; sidecar metadata (categories / description /
        # severity) sits below so mute/beep/blur edits show the useful fields up top.
        self._form.addRow("Enabled", self._enabled_check)
        self._form.addRow("Type", self._type_combo)
        self._form.addRow("Channels", self._channels_block)
        self._form.addRow("Tone", self._beep_combo)
        self._form.addRow("Gain", self._gain_spin)
        self._form.addRow("Blur radius", self._radius_spin)
        self._form.addRow("Pixel size", self._size_spin)
        self._form.addRow("Zoom/crop scale", self._scale_spin)
        self._form.addRow("Categories", self._categories)
        self._form.addRow("Description", self._description_edit)
        self._form.addRow("Severity", self._severity_combo)
        self._language_label = QLabel()
        self._language_label.setObjectName("editLanguage")
        self._language_label.setAlignment(
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
        )
        self._language_label.setToolTip(
            "Audio tracks in this language share this edit. "
            "Change it from the timeline audio menu."
        )
        apply_muted_text(self._language_label, extra_css="font-style: italic;")
        self._form.addRow("Language", self._language_label)
        for field in (
            self._type_combo,
            self._categories.search_field,
            self._description_edit,
            self._severity_combo,
            self._beep_combo,
            self._gain_spin,
            self._radius_spin,
            self._size_spin,
            self._scale_spin,
            self._language_label,
        ):
            field.setMinimumHeight(_FIELD_HEIGHT)
            field.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        editor_layout.addWidget(form_host, alignment=Qt.AlignmentFlag.AlignTop)
        editor_layout.addStretch(1)

        self._empty_state = QWidget()
        self._empty_state.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding
        )

        self._inspector_stack = QStackedWidget()
        self._inspector_stack.addWidget(self._empty_state)
        self._inspector_stack.addWidget(self._editor)

        self._inspector = QScrollArea()
        self._inspector.setWidgetResizable(True)
        self._inspector.setFrameShape(QFrame.Shape.NoFrame)
        self._inspector.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._inspector.setWidget(self._inspector_stack)
        self._inspector.setMinimumWidth(_INSPECTOR_MIN_WIDTH)
        self._inspector.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

        self._splitter = QSplitter(Qt.Orientation.Horizontal)
        self._splitter.setChildrenCollapsible(False)
        list_column = QWidget()
        list_col = QVBoxLayout(list_column)
        list_col.setContentsMargins(0, 0, 0, 0)
        list_col.setSpacing(4)
        list_col.addLayout(heading)
        list_col.addWidget(self._list, stretch=1)
        self._splitter.addWidget(list_column)
        self._splitter.addWidget(self._inspector)
        self._splitter.setStretchFactor(0, 3)
        self._splitter.setStretchFactor(1, 2)
        layout.addWidget(self._splitter, stretch=1)

        self._list.current_op_changed.connect(self._on_list_current)
        self._list.row_clicked.connect(self._on_list_reclicked)
        self._list.delete_requested.connect(self._emit_delete_op)
        self._list.toggle_enabled_requested.connect(self._toggle_enabled_from_list)
        self._enabled_check.toggled.connect(self._emit_enabled_changed)
        self._type_combo.currentIndexChanged.connect(self._emit_type_changed)
        self._categories.categories_changed.connect(self._emit_meta_changed)
        self._description_edit.editingFinished.connect(self._emit_meta_changed)
        self._severity_combo.currentIndexChanged.connect(self._emit_meta_changed)
        self._beep_combo.currentIndexChanged.connect(self._emit_param_fields)
        self._gain_spin.valueChanged.connect(self._emit_param_fields)
        self._radius_spin.valueChanged.connect(self._emit_param_fields)
        self._size_spin.valueChanged.connect(self._emit_param_fields)
        self._scale_spin.valueChanged.connect(self._emit_param_fields)

        self._current_id: str | None = None
        self._current_op_type = OperationType.MUTE
        self._ops_by_id: dict[str, Operation] = {}
        self._show_operation_editor(False)
        self._channel_names: list[str] = []
        self._track_languages: dict[int, tuple[str, bool]] = {}
        self._channel_layout: str | None = None
        self._channel_count = 0
        self._channel_checks: dict[str, ChannelSpeakerHandle] = {}
        self._per_channel_editing = False
        self._full_mix_audio = False
        self._loading_editor = False
        self._block_selection_signal = False
        self._syncing_playhead = False
        self._categories.set_audio_filter(True)

    def _rebuild_channel_checks(self, names: list[str]) -> None:
        if names == list(self._channel_checks):
            return
        self._channels_widget.set_channels(names)
        self._channel_checks = self._channels_widget.checks

    def load_operations(
        self,
        operations: list[Operation],
        *,
        strategy: DialogueTargetStrategy | None = None,
        channel_layout: str | None = None,
        channels: int = 0,
        track_languages: dict[int, tuple[str, bool]] | None = None,
    ) -> None:
        self._track_languages = dict(track_languages or {})
        per_channel = strategy == DialogueTargetStrategy.CENTER_CHANNEL
        self._per_channel_editing = per_channel
        self._full_mix_audio = strategy == DialogueTargetStrategy.FULL_MIX
        self._channel_layout = channel_layout
        self._channel_count = channels
        self._channel_names = (
            editable_channel_names(channel_layout, channels)
            if channel_layout or channels
            else []
        )
        self._rebuild_channel_checks(self._channel_names)

        keep = self._current_id
        self._ops_by_id = {op.id: op for op in operations}
        count = len(operations)
        self._count_label.setText("" if count == 0 else f"({count})")
        self._block_selection_signal = True
        try:
            self._list.set_operations(operations)
            if keep is not None and self._list.row_for_id(keep) >= 0:
                self._list.select_row(self._list.row_for_id(keep))
                self._current_id = keep
                kept = self._ops_by_id.get(keep)
                if kept is not None:
                    self.update_operation_editor(kept)
            else:
                self._current_id = None
                self._show_operation_editor(False)
        finally:
            self._block_selection_signal = False

    def refresh_row(self, op: Operation) -> None:
        self._ops_by_id[op.id] = op
        self._list.refresh_operation(op)
        if op.id == self._current_id:
            self.update_operation_editor(op)

    def _row_for_op_id(self, op_id: str) -> int:
        return self._list.row_for_id(op_id)

    def follow_playhead(self, seconds: float) -> None:
        """Select the censor region at ``seconds`` without seeking the timeline."""
        operations = sorted(
            self._ops_by_id.values(),
            key=lambda op: op.time_range.start.seconds,
        )
        if not operations:
            return

        def _covers(op: Operation) -> bool:
            start = float(op.time_range.start.seconds)
            end = float(op.time_range.end.seconds)
            return start <= seconds < end

        op: Operation | None = None
        if self._current_id is not None:
            current = self._ops_by_id.get(self._current_id)
            if current is not None and _covers(current):
                op = current
        if op is None:
            op = timed_item_for_playhead(
                operations,
                seconds,
                start_seconds=lambda item: float(item.time_range.start.seconds),
                end_seconds=lambda item: float(item.time_range.end.seconds),
            )
        if op is None:
            return
        row = self._list.row_for_id(op.id)
        if row < 0:
            return
        if self._list.current_row() == row:
            self._list.select_row(row, scroll=True)
            return
        self._syncing_playhead = True
        try:
            self._block_selection_signal = True
            self._list.select_row(row, scroll=True)
            self._current_id = op.id
            self._current_op_type = op.type
        finally:
            self._block_selection_signal = False
            self._syncing_playhead = False

    def select_operation(self, op_id: str, *, emit_selection_changed: bool = True) -> None:
        self._block_selection_signal = not emit_selection_changed
        row = self._list.row_for_id(op_id)
        if row >= 0:
            self._list.select_row(row, scroll=False)
            if not emit_selection_changed:
                self._current_id = op_id
        self._block_selection_signal = False

    def clear_selection(self) -> None:
        self._block_selection_signal = True
        self._list.select_row(-1)
        self._current_id = None
        self._block_selection_signal = False
        self._show_operation_editor(False)

    def _on_list_current(self, op_id: str) -> None:
        if not op_id:
            if not self._block_selection_signal:
                self._current_id = None
                self._show_operation_editor(False)
            return
        self._current_id = op_id
        op = self._ops_by_id.get(op_id)
        if op is not None:
            self._current_op_type = op.type
            self.update_operation_editor(op)
        if not self._block_selection_signal and not self._syncing_playhead:
            self.selection_changed.emit(op_id)

    def _on_list_reclicked(self, op_id: str) -> None:
        if self._block_selection_signal or self._syncing_playhead:
            return
        self._current_id = op_id
        op = self._ops_by_id.get(op_id)
        if op is not None:
            self._current_op_type = op.type
            self.update_operation_editor(op)
        self.selection_changed.emit(op_id)

    def _sync_language_label(self, op: Operation) -> None:
        audio = is_audio_operation(op.type)
        self._set_form_row_visible(self._language_label, audio)
        if not audio:
            return
        self._language_label.setText(
            edit_language_label(
                op.language,
                track_language=self._track_languages.get(op.scope.stream_index),
            )
        )

    def _sync_channel_editor(self, op: Operation) -> None:
        audio = is_audio_operation(op.type)
        show_checks = audio and bool(self._channel_names)
        self._set_form_row_visible(self._channels_block, show_checks)
        if not show_checks:
            return
        shown = channels_for_layout(
            op.scope.channels,
            self._channel_layout,
            self._channel_count,
        )
        if shown is None:
            checks = {name: False for name in self._channel_names}
        elif not shown and self._full_mix_audio:
            checks = {name: True for name in self._channel_names}
        else:
            checks = checks_from_scope_channels(shown or [], self._channel_names)
        for name, box in self._channel_checks.items():
            box.setChecked(checks.get(name, False))

    def _emit_channels_changed(self, *_args: object) -> None:
        if self._loading_editor or self._current_id is None:
            return
        op = self._ops_by_id.get(self._current_id)
        if op is None or not is_audio_operation(op.type):
            return
        channels = [
            name for name, box in self._channel_checks.items() if box.isChecked()
        ]
        self.channels_changed.emit(self._current_id, channels)

    def _toggle_all_speakers(self, *_args: object) -> None:
        if self._loading_editor or self._current_id is None:
            return
        self._channels_widget.toggle_all()

    def update_operation_editor(self, op: Operation | None) -> None:
        if op is None:
            self._current_id = None
            self._show_operation_editor(False)
            return
        self._current_id = op.id
        self._current_op_type = op.type
        self._loading_editor = True
        try:
            self._show_operation_editor(True)
            self._enabled_check.setChecked(op.enabled)
            index = self._type_combo.findData(op.type.value)
            if index < 0:
                index = self._type_combo.findData(OperationType.MUTE.value)
            self._type_combo.setCurrentIndex(max(0, index))
            self._categories.set_audio_filter(is_audio_operation(op.type))
            self._categories.set_categories(resolve_categories(op.categories))
            self._description_edit.setText(op.description or "")
            populate_severity_combo(self._severity_combo, op.severity)
            params = op.params
            populate_beep_tone_combo(
                self._beep_combo,
                float(params.get("beep_freq", op.censor_params().beep_freq)),
            )
            self._gain_spin.setValue(float(params.get("gain", op.censor_params().gain)))
            self._radius_spin.setValue(float(params.get("radius", 8.0)))
            self._size_spin.setValue(float(params.get("size", 16.0)))
            self._scale_spin.setValue(float(params.get("scale", 2.0)))
            self._sync_channel_editor(op)
            self._sync_language_label(op)
            self._set_form_row_visible(self._beep_combo, op.type == OperationType.BEEP)
            self._set_form_row_visible(self._gain_spin, op.type in {OperationType.VOLUME, OperationType.BEEP})
            self._set_form_row_visible(self._radius_spin, op.type == OperationType.BLUR)
            self._set_form_row_visible(self._size_spin, op.type == OperationType.PIXELATE)
            self._set_form_row_visible(
                self._scale_spin, op.type in {OperationType.ZOOM, OperationType.CROP}
            )
        finally:
            self._loading_editor = False

    def _emit_type_changed(self, *_args: object) -> None:
        if self._loading_editor or self._current_id is None:
            return
        op = self._ops_by_id.get(self._current_id)
        if op is None:
            return
        op_type = normalize_operation_type(str(self._type_combo.currentData()))
        self._current_op_type = op_type
        self._categories.set_audio_filter(is_audio_operation(op_type))
        self.censor_params_changed.emit(op.id, op_type, op.censor_params())

    def current_operation_id(self) -> str | None:
        return self._current_id

    def toggle_enabled(self, op_id: str) -> None:
        self._current_id = op_id
        self._apply_enabled_toggle(op_id)

    def _toggle_enabled_from_list(self, op_id: str) -> None:
        self.toggle_enabled(op_id)

    def _apply_enabled_toggle(self, op_id: str) -> None:
        op = self._ops_by_id.get(op_id)
        if op is None:
            return
        enabled = not op.enabled
        self._loading_editor = True
        self._enabled_check.setChecked(enabled)
        self._loading_editor = False
        op.enabled = enabled
        self._list.refresh_operation(op)
        self.enabled_changed.emit(op.id, enabled)

    def _emit_enabled_changed(self, checked: bool) -> None:
        if self._loading_editor or self._current_id is None:
            return
        op = self._ops_by_id.get(self._current_id)
        if op is None:
            return
        op.enabled = bool(checked)
        self._list.refresh_operation(op)
        self.enabled_changed.emit(op.id, op.enabled)

    def _emit_meta_changed(self, *_args: object) -> None:
        if self._loading_editor or self._current_id is None:
            return
        op = self._ops_by_id.get(self._current_id)
        if op is None:
            return
        cat_ids = self._categories.selected_ids()
        if not cat_ids:
            cat_ids = [OTHER_ID]
            self._categories.set_categories(cat_ids)
        op.categories = resolve_categories(cat_ids)
        text = self._description_edit.text().strip()
        op.description = text or None
        raw = self._severity_combo.currentData()
        op.severity = int(raw) if raw is not None else None
        self._list.refresh_operation(op)
        self.operation_meta_changed.emit(self._current_id)

    def _emit_param_fields(self, *_args: object) -> None:
        if self._loading_editor or self._current_id is None:
            return
        op = self._ops_by_id.get(self._current_id)
        if op is None:
            return
        if op.type == OperationType.VOLUME:
            op.params["gain"] = float(self._gain_spin.value())
            self.censor_params_changed.emit(op.id, op.type, op.censor_params())
        elif op.type == OperationType.BEEP:
            freq = self._beep_combo.currentData()
            if freq is not None:
                op.params["beep_freq"] = float(freq)
            op.params["gain"] = float(self._gain_spin.value())
            self.censor_params_changed.emit(op.id, op.type, op.censor_params())
        elif op.type == OperationType.BLUR:
            op.params["radius"] = float(self._radius_spin.value())
            self.operation_meta_changed.emit(op.id)
        elif op.type == OperationType.PIXELATE:
            op.params["size"] = float(self._size_spin.value())
            self.operation_meta_changed.emit(op.id)
        elif op.type in {OperationType.ZOOM, OperationType.CROP}:
            op.params["scale"] = float(self._scale_spin.value())
            self.operation_meta_changed.emit(op.id)

    def _set_form_row_visible(self, field: QWidget, visible: bool) -> None:
        row = self._form.getWidgetPosition(field)[0]
        if row < 0:
            field.setVisible(visible)
            return
        self._form.setRowVisible(row, visible)

    def _show_operation_editor(self, visible: bool) -> None:
        if visible:
            self._inspector_stack.setCurrentWidget(self._editor)
            return
        self._inspector_stack.setCurrentWidget(self._empty_state)

    def _emit_delete_op(self, op_id: str) -> None:
        self._current_id = op_id
        self.delete_requested.emit(op_id)
