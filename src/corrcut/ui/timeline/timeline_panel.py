"""Timeline dock: tool strip, then playhead time and zoom, then the waveform."""

from __future__ import annotations

from PySide6.QtCore import QEvent, QObject, QSize, Qt, QTimer, Signal
from PySide6.QtGui import (
    QAction,
    QActionGroup,
    QFocusEvent,
    QFont,
    QKeyEvent,
    QMouseEvent,
    QPalette,
)
from PySide6.QtWidgets import (
    QApplication,
    QButtonGroup,
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMenu,
    QSizePolicy,
    QSlider,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from corrcut.domain.operation import Operation, OperationType
from corrcut.ui.beep_tone_combo import populate_beep_tone_combo
from corrcut.ui.buttons import use_pill_button
from corrcut.ui.icons import app_icon
from corrcut.ui.timeline.timecode import format_timecode, parse_timecode
from corrcut.ui.timeline.timeline_mode import (
    TOOL_STRIP_ITEMS,
    TimelineMode,
    tool_status_text,
)
from corrcut.ui.timeline.timeline_widget import TimelineWidget
from corrcut.ui.timeline.zoom_mapping import (
    SLIDER_STEPS,
    pixels_per_second_from_slider,
    slider_from_pixels_per_second,
)

# Icon toggles (snap / scene cuts): no fill — on state is the highlight-colored glyph.
_ICON_TOGGLE_STYLE = """
QToolButton {
    background: transparent;
    border: none;
    padding: 0;
}
QToolButton:hover {
    background: transparent;
}
QToolButton:pressed {
    background: transparent;
}
QToolButton:checked {
    background: transparent;
    border: none;
}
"""

# Neutral chrome — not the blur/highlight accent Windows QSlider would use.
# Groove stays darker than the window panel (#1e1e1e). Sub-page matches it
# so the track stays uniform; only the thumb moves.
_ZOOM_SLIDER_STYLE = """
QSlider#timelineZoomSlider::groove:horizontal {
    height: 4px;
    background: #0a0a0a;
    border-radius: 2px;
}
QSlider#timelineZoomSlider::sub-page:horizontal {
    background: #0a0a0a;
    border-radius: 2px;
}
QSlider#timelineZoomSlider::add-page:horizontal {
    background: #0a0a0a;
    border-radius: 2px;
}
QSlider#timelineZoomSlider::handle:horizontal {
    border: none;
    width: 12px;
    height: 12px;
    margin: -4px 0;
    border-radius: 6px;
}
QSlider#timelineZoomSlider::handle:horizontal:disabled {
    background: #454545;
}
QSlider#timelineZoomSlider::handle:horizontal:enabled {
    background: #8a8a8a;
}
"""

# Readout until a double-click; the box appears only while a time is being typed.
_PLAYHEAD_TIME_STYLE = """
QLineEdit#timelinePlayhead {
    border: 1px solid transparent;
    background: transparent;
    padding: 0px 2px;
}
QLineEdit#timelinePlayhead:!read-only {
    border: 1px solid #5c6370;
    background: #252830;
    border-radius: 3px;
}
"""


class _PlayheadTimecodeEdit(QLineEdit):
    """Playhead time that looks like a label until it is double-clicked."""

    seek_requested = Signal()
    edit_cancelled = Signal()

    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("timelinePlayhead")
        self.setFrame(False)
        self.setReadOnly(True)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setPlaceholderText("HH:MM:SS.mmm")
        self.setToolTip("Playhead — double-click to type a time, Enter to seek")
        self.setStyleSheet(_PLAYHEAD_TIME_STYLE)
        font = QFont(self.font())
        if font.pointSizeF() > 0:
            font.setPointSizeF(font.pointSizeF() + 2)
        else:
            font.setPixelSize(max(1, font.pixelSize()) + 3)
        self.setFont(font)
        sample = "00:00:00.000"
        self.setFixedWidth(self.fontMetrics().horizontalAdvance(sample) + 16)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self.setCursor(Qt.CursorShape.ArrowCursor)
        self._closing = False
        self._arm_select = False
        self._text_before_edit = ""
        self._outside_filter = False
        self._editing_enabled = True

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if self.isReadOnly():
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        # The release after a double-click would otherwise drop the selection.
        if self._arm_select:
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def set_editing_enabled(self, enabled: bool) -> None:
        self._editing_enabled = bool(enabled)
        if not self._editing_enabled and not self.isReadOnly():
            self._closing = True
            try:
                self.edit_cancelled.emit()
                self._leave_edit_appearance()
                self.clearFocus()
            finally:
                self._closing = False

    def mouseDoubleClickEvent(self, event: QMouseEvent) -> None:
        if not self._editing_enabled:
            event.accept()
            return
        if event.button() == Qt.MouseButton.LeftButton:
            self._begin_edit()
            event.accept()
            return
        super().mouseDoubleClickEvent(event)

    def _begin_edit(self) -> None:
        self._text_before_edit = self.text()
        self._arm_select = True
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setReadOnly(False)
        self.setCursor(Qt.CursorShape.IBeamCursor)
        self.setFocus(Qt.FocusReason.MouseFocusReason)
        self.selectAll()
        QTimer.singleShot(0, self._select_editing_text)
        self._watch_outside_clicks()

    def _watch_outside_clicks(self) -> None:
        if self._outside_filter:
            return
        app = QApplication.instance()
        if app is None:
            return
        app.installEventFilter(self)
        self._outside_filter = True

    def _stop_watching_outside_clicks(self) -> None:
        if not self._outside_filter:
            return
        app = QApplication.instance()
        if app is not None:
            app.removeEventFilter(self)
        self._outside_filter = False

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        if (
            self._outside_filter
            and event.type() == QEvent.Type.MouseButtonPress
            and isinstance(event, QMouseEvent)
            and not self.rect().contains(self.mapFromGlobal(event.globalPosition().toPoint()))
        ):
            # Empty chrome and other non-focusable areas do not take focus on their own.
            self.clearFocus()
        return super().eventFilter(watched, event)

    def _select_editing_text(self) -> None:
        self._arm_select = False
        if not self.isReadOnly():
            self.selectAll()

    def _leave_edit_appearance(self) -> None:
        self._stop_watching_outside_clicks()
        self.setReadOnly(True)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setCursor(Qt.CursorShape.ArrowCursor)

    def focusOutEvent(self, event: QFocusEvent) -> None:
        edited = self.text() != self._text_before_edit
        self._leave_edit_appearance()
        super().focusOutEvent(event)
        if self._closing:
            return
        if edited:
            self.seek_requested.emit()
        else:
            self.edit_cancelled.emit()

    def keyPressEvent(self, event: QKeyEvent) -> None:
        if self.isReadOnly():
            super().keyPressEvent(event)
            return
        key = event.key()
        if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self.seek_requested.emit()
            return
        if key == Qt.Key.Key_Escape:
            self._closing = True
            try:
                self.edit_cancelled.emit()
                self._leave_edit_appearance()
                self.clearFocus()
            finally:
                self._closing = False
            return
        super().keyPressEvent(event)

    def relinquish_focus(self) -> None:
        """Leave edit mode without treating the blur as a second seek."""
        self._closing = True
        try:
            self._leave_edit_appearance()
            self.clearFocus()
        finally:
            self._closing = False


class TimelinePanel(QWidget):
    """Waveform/timeline with a tool strip and a separate playhead/zoom row."""

    beep_tone_changed = Signal(float)
    tool_mode_changed = Signal(object)  # TimelineMode
    tool_label_changed = Signal(str)
    audio_track_selected = Signal(int)
    audio_language_selected = Signal(str)
    scene_cuts_snap_changed = Signal(bool)

    def __init__(self) -> None:
        super().__init__()
        self._timeline = TimelineWidget()

        self._zoom_slider = QSlider(Qt.Orientation.Horizontal)
        self._zoom_slider.setObjectName("timelineZoomSlider")
        self._zoom_slider.setRange(0, SLIDER_STEPS)
        self._zoom_slider.setValue(
            slider_from_pixels_per_second(self._timeline.pixels_per_second())
        )
        self._zoom_slider.setToolTip("Timeline zoom")
        self._zoom_slider.setMaximumWidth(200)
        self._zoom_slider.setFixedWidth(200)
        self._zoom_slider.setStyleSheet(_ZOOM_SLIDER_STYLE)
        self._pending_slider = self._zoom_slider.value()
        self._zoom_timer = QTimer(self)
        self._zoom_timer.setSingleShot(True)
        self._zoom_timer.setInterval(50)
        self._zoom_timer.timeout.connect(self._apply_pending_zoom)

        self._zoom_icon = QLabel()
        self._zoom_icon.setObjectName("timelineZoomIcon")
        self._zoom_icon.setPixmap(app_icon("mdi6.magnify").pixmap(QSize(16, 16)))
        self._zoom_icon.setFixedSize(18, 18)
        self._zoom_icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._zoom_icon.setToolTip("Timeline zoom")

        self._fit_btn = QToolButton()
        self._fit_btn.setText("Fit")
        self._fit_btn.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
        use_pill_button(self._fit_btn)
        self._fit_btn.setToolTip("Fit entire clip in the timeline width")
        self._fit_btn.clicked.connect(self._timeline.fit_to_viewport)

        self._timecode_edit = _PlayheadTimecodeEdit()

        self._tools_enabled = True
        self._tool_status_text = tool_status_text(TimelineMode.SELECT)
        self._tool_group = QButtonGroup(self)
        self._tool_group.setExclusive(True)
        self._tool_buttons: dict[TimelineMode, QToolButton] = {}
        self._tools_bar = QWidget()
        self._tools_bar.setObjectName("timelineToolsBar")
        tools_row = QHBoxLayout(self._tools_bar)
        tools_row.setContentsMargins(8, 6, 8, 6)
        tools_row.setSpacing(6)
        for index, item in enumerate(TOOL_STRIP_ITEMS):
            btn = QToolButton()
            btn.setText(item.label)
            btn.setToolTip(item.tip)
            btn.setCheckable(True)
            use_pill_button(btn)
            btn.setMinimumHeight(28)
            self._tool_group.addButton(btn, index)
            self._tool_buttons[item.mode] = btn
            tools_row.addWidget(btn)
            if item.mode is TimelineMode.SELECT:
                btn.setChecked(True)
            # Visual break after Beep (audio) and Blank (cut).
            if item.mode in {TimelineMode.CREATE_BEEP, TimelineMode.CREATE_BLANK}:
                sep = QLabel("│")
                sep.setStyleSheet("color: #666;")
                tools_row.addWidget(sep)
        self._tool_group.idClicked.connect(self._on_tool_id_clicked)

        snap_sep = QLabel("│")
        snap_sep.setStyleSheet("color: #666;")
        self._snap_btn = QToolButton()
        self._snap_btn.setObjectName("timelineSnapToggle")
        self._snap_btn.setIconSize(QSize(18, 18))
        self._snap_btn.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonIconOnly)
        self._snap_btn.setToolTip("Snap edits to the playhead and other edits while dragging")
        self._snap_btn.setCheckable(True)
        self._snap_btn.setChecked(True)
        self._snap_btn.setAutoRaise(True)
        self._snap_btn.setFixedSize(28, 28)
        self._snap_btn.setStyleSheet(_ICON_TOGGLE_STYLE)
        self._wire_icon_toggle(
            self._snap_btn,
            "mdi6.magnet",
            self._timeline.set_magnetic_snap_enabled,
            rotated=45.0,
        )

        self._scene_cuts_btn = QToolButton()
        self._scene_cuts_btn.setObjectName("timelineSceneCutsToggle")
        self._scene_cuts_btn.setIconSize(QSize(18, 18))
        self._scene_cuts_btn.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonIconOnly)
        self._scene_cuts_btn.setToolTip(
            "Show scene-cut markers; with the magnet on, snap edit edges to cuts"
        )
        self._scene_cuts_btn.setCheckable(True)
        self._scene_cuts_btn.setChecked(True)
        self._scene_cuts_btn.setAutoRaise(True)
        self._scene_cuts_btn.setFixedSize(28, 28)
        self._scene_cuts_btn.setStyleSheet(_ICON_TOGGLE_STYLE)
        self._wire_icon_toggle(
            self._scene_cuts_btn,
            "mdi6.content-cut",
            self._on_scene_cuts_btn_toggled,
        )

        self._beep_tone_label = QLabel("Tone")

        self._beep_tone_combo = QComboBox()
        self._beep_tone_combo.setMinimumWidth(120)
        self._beep_tone_combo.setToolTip("Beep tone for selected beep region")
        self._beep_tone_loading = False
        self._beep_tone_combo.currentIndexChanged.connect(self._emit_beep_tone)

        tools_row.addWidget(snap_sep)
        tools_row.addWidget(self._snap_btn)
        tools_row.addWidget(self._scene_cuts_btn)
        tools_row.addStretch(1)
        tools_row.addWidget(self._beep_tone_label)
        tools_row.addWidget(self._beep_tone_combo)

        self._tools_rule = QFrame()
        self._tools_rule.setObjectName("timelineToolsRule")
        self._tools_rule.setFixedHeight(1)
        self._tools_rule.setFrameShape(QFrame.Shape.NoFrame)
        self._tools_rule.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self._tools_rule.setStyleSheet(
            "QFrame#timelineToolsRule { background-color: #161616; border: none;}"
        )

        self._chrome_bar = QWidget()
        self._chrome_bar.setObjectName("timelineChromeBar")
        chrome_row = QHBoxLayout(self._chrome_bar)
        chrome_row.setContentsMargins(4, 6, 4, 4)
        chrome_row.setSpacing(6)
        self._audio_track_btn = QToolButton()
        self._audio_track_btn.setObjectName("timelineAudioTrack")
        self._audio_track_btn.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self._audio_track_btn.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
        self._audio_track_btn.setText("No audio")
        self._audio_track_btn.setToolTip("Audio track these edits apply to")
        use_pill_button(self._audio_track_btn)
        self._audio_track_menu = QMenu(self._audio_track_btn)
        self._audio_track_btn.setMenu(self._audio_track_menu)
        self._audio_track_btn.setEnabled(False)

        chrome_row.addWidget(self._timecode_edit)
        chrome_row.addWidget(self._audio_track_btn)
        chrome_row.addStretch(1)
        chrome_row.addWidget(self._zoom_icon)
        chrome_row.addWidget(self._zoom_slider)
        chrome_row.addWidget(self._fit_btn)

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        root.addWidget(self._tools_bar)
        root.addWidget(self._tools_rule)
        root.addWidget(self._chrome_bar)
        root.addWidget(self._timeline, stretch=1)
        self._sync_maximum_height()

        self._syncing_tool = False
        self._zoom_slider.valueChanged.connect(self._schedule_zoom)
        self._timeline.zoom_changed.connect(self._on_timeline_zoom_changed)
        self._timeline.playhead_changed.connect(self._on_playhead_changed)
        self._timecode_edit.seek_requested.connect(self._commit_timecode)
        self._timecode_edit.edit_cancelled.connect(self._cancel_timecode)
        self._update_timecode_display(self._timeline.playhead_seconds())
        self._set_beep_tone_visible(False)

    def _wire_icon_toggle(
        self,
        button: QToolButton,
        icon_name: str,
        on_toggled,
        *,
        rotated: float = 0.0,
    ) -> None:
        def _on_toggled(checked: bool) -> None:
            self._apply_icon_toggle(button, icon_name, checked, rotated=rotated)
            on_toggled(checked)

        button.toggled.connect(_on_toggled)
        self._apply_icon_toggle(button, icon_name, button.isChecked(), rotated=rotated)

    def _apply_icon_toggle(
        self,
        button: QToolButton,
        icon_name: str,
        checked: bool,
        *,
        rotated: float = 0.0,
    ) -> None:
        if checked:
            color = button.palette().color(QPalette.ColorRole.Highlight)
            button.setIcon(app_icon(icon_name, color=color, rotated=rotated))
        else:
            button.setIcon(app_icon(icon_name, muted=True, rotated=rotated))

    def set_tools_enabled(self, enabled: bool) -> None:
        """Gray out the edit tools until a movie is loaded."""
        enabled = bool(enabled)
        self._tools_enabled = enabled
        for btn in self._tool_buttons.values():
            btn.setEnabled(enabled)
        self._snap_btn.setEnabled(enabled)
        self._scene_cuts_btn.setEnabled(enabled)
        self._audio_track_btn.setEnabled(enabled and self._audio_track_menu.actions() != [])
        self._beep_tone_combo.setEnabled(enabled)
        self._zoom_slider.setEnabled(enabled)
        self._fit_btn.setEnabled(enabled)
        self._timecode_edit.set_editing_enabled(enabled)

    def tools_enabled(self) -> bool:
        return self._tools_enabled

    def set_audio_track_choices(
        self,
        entries: list[tuple[int, str]],
        current: int | None,
        button_text: str,
        languages: list[tuple[str, str]],
        current_language: str,
        *,
        override_set: bool,
    ) -> None:
        """Rebuild the timeline audio menu. Language "" clears a per-track override."""
        menu = self._audio_track_menu
        menu.clear()
        track_group = QActionGroup(menu)
        track_group.setExclusive(True)
        for stream_index, label in entries:
            act = QAction(label, menu)
            act.setCheckable(True)
            act.setChecked(stream_index == current)
            track_group.addAction(act)
            act.triggered.connect(
                lambda _checked=False, index=stream_index: self.audio_track_selected.emit(index)
            )
            menu.addAction(act)
        if entries and languages:
            menu.addSeparator()
            language_menu = menu.addMenu("Language")
            language_group = QActionGroup(language_menu)
            language_group.setExclusive(True)
            for code, name in languages:
                act = QAction(name, language_menu)
                act.setCheckable(True)
                act.setChecked(code == current_language)
                language_group.addAction(act)
                act.triggered.connect(
                    lambda _checked=False, chosen=code: self.audio_language_selected.emit(chosen)
                )
                language_menu.addAction(act)
            language_menu.addSeparator()
            clear = QAction("Use file language", language_menu)
            clear.setEnabled(override_set)
            clear.triggered.connect(lambda _checked=False: self.audio_language_selected.emit(""))
            language_menu.addAction(clear)
        self._audio_track_btn.setText(button_text or "No audio")
        tip = "Audio track these edits apply to"
        if "(assumed)" in button_text:
            tip = "Language was not in the file, so edits use English. Change it from this menu."
        self._audio_track_btn.setToolTip(tip)
        self._audio_track_btn.setEnabled(self._tools_enabled and bool(entries))

    def _on_tool_id_clicked(self, button_id: int) -> None:
        if self._syncing_tool or not self._tools_enabled:
            return
        if 0 <= button_id < len(TOOL_STRIP_ITEMS):
            mode = TOOL_STRIP_ITEMS[button_id].mode
            op_type = mode.create_operation_type
            if op_type is not None:
                self._timeline.set_draw_operation_type(op_type)
            self._publish_tool_label(mode)
            self.tool_mode_changed.emit(mode)

    def _on_scene_cuts_btn_toggled(self, enabled: bool) -> None:
        self._timeline.set_snap_to_scene_cuts_enabled(bool(enabled))
        self.scene_cuts_snap_changed.emit(bool(enabled))

    def set_scene_cuts_snap_enabled(self, enabled: bool) -> None:
        """Sync toolbar button + timeline (does not emit scene_cuts_snap_changed)."""
        enabled = bool(enabled)
        self._scene_cuts_btn.blockSignals(True)
        self._scene_cuts_btn.setChecked(enabled)
        self._scene_cuts_btn.blockSignals(False)
        self._apply_icon_toggle(self._scene_cuts_btn, "mdi6.content-cut", enabled)
        self._timeline.set_snap_to_scene_cuts_enabled(enabled)

    def set_tool_mode(self, mode: TimelineMode) -> None:
        self._publish_tool_label(mode)
        btn = self._tool_buttons.get(mode)
        if btn is None or btn.isChecked():
            return
        self._syncing_tool = True
        try:
            btn.setChecked(True)
        finally:
            self._syncing_tool = False

    def tool_status_text(self) -> str:
        return self._tool_status_text

    def _publish_tool_label(self, mode: TimelineMode) -> None:
        text = tool_status_text(mode)
        if text == self._tool_status_text:
            return
        self._tool_status_text = text
        self.tool_label_changed.emit(text)

    def set_beep_tone_for_operation(self, op: Operation | None) -> None:
        if op is None or op.type != OperationType.BEEP:
            self._set_beep_tone_visible(False)
            return
        self._set_beep_tone_visible(True)
        self._beep_tone_loading = True
        try:
            populate_beep_tone_combo(self._beep_tone_combo, op.censor_params().beep_freq)
        finally:
            self._beep_tone_loading = False

    def _set_beep_tone_visible(self, visible: bool) -> None:
        self._beep_tone_label.setVisible(visible)
        self._beep_tone_combo.setVisible(visible)

    def _emit_beep_tone(self, _index: int) -> None:
        if self._beep_tone_loading:
            return
        freq = float(self._beep_tone_combo.currentData())
        self.beep_tone_changed.emit(freq)

    def showEvent(self, event) -> None:  # noqa: ANN001, N802
        super().showEvent(event)
        self._sync_maximum_height()

    def resizeEvent(self, event) -> None:  # noqa: ANN001, N802
        super().resizeEvent(event)
        self._sync_maximum_height()

    def _chrome_height(self) -> int:
        widgets = (self._tools_bar, self._tools_rule, self._chrome_bar)
        if self.isVisible() and all(widget.height() > 0 for widget in widgets):
            chrome = sum(widget.height() for widget in widgets)
        else:
            chrome = sum(widget.sizeHint().height() for widget in widgets)
        margins = self.layout().contentsMargins()
        return chrome + margins.top() + margins.bottom()

    def _sync_maximum_height(self) -> None:
        """Stop the dock at both tracks fully open, so nothing empty sits under them."""
        desired = self._chrome_height() + self._timeline.maximumHeight()
        if desired != self.maximumHeight():
            self.setMaximumHeight(desired)

    @property
    def timeline(self) -> TimelineWidget:
        return self._timeline

    def sync_playhead_display(self, seconds: float, *, auto_scroll: bool = False) -> None:
        self._timeline.set_playhead(seconds, notify=False)
        if auto_scroll:
            self._timeline.scroll_playhead_keep_visible()
        self._update_timecode_display(seconds)

    def _on_playhead_changed(self, seconds: float) -> None:
        self._update_timecode_display(seconds)

    def _update_timecode_display(self, seconds: float, *, force: bool = False) -> None:
        if not force and not self._timecode_edit.isReadOnly():
            return
        self._timecode_edit.setText(format_timecode(seconds))

    def _commit_timecode(self) -> None:
        parsed = parse_timecode(self._timecode_edit.text())
        current = self._timeline.playhead_seconds()
        if parsed is None:
            if not self._timecode_edit.isReadOnly():
                self._timecode_edit.selectAll()
                return
            self._update_timecode_display(current, force=True)
            return
        if abs(parsed - current) > 0.0005:
            self._timeline.go_to_time(parsed, scroll_into_view=True)
            current = self._timeline.playhead_seconds()
        self._update_timecode_display(current, force=True)
        if self._timecode_edit.hasFocus():
            self._timecode_edit.relinquish_focus()

    def _cancel_timecode(self) -> None:
        self._update_timecode_display(self._timeline.playhead_seconds(), force=True)

    def _schedule_zoom(self, value: int) -> None:
        if not self._tools_enabled:
            return
        self._pending_slider = value
        self._zoom_timer.start()

    def _apply_pending_zoom(self) -> None:
        vw = self._timeline.viewport().width()
        pps = pixels_per_second_from_slider(self._pending_slider, float(vw))
        self._timeline.set_pixels_per_second(
            pps,
            anchor_view_x=self._timeline.playhead_viewport_x(),
        )

    def _zoom_step(self, factor: float) -> None:
        self._timeline.set_pixels_per_second(
            self._timeline.pixels_per_second() * factor,
            anchor_view_x=self._timeline.playhead_viewport_x(),
        )

    def zoom_in(self) -> None:
        if self._tools_enabled:
            self._zoom_step(1.25)

    def zoom_out(self) -> None:
        if self._tools_enabled:
            self._zoom_step(1 / 1.25)

    def _on_timeline_zoom_changed(self, pps: float) -> None:
        value = slider_from_pixels_per_second(pps, float(self._timeline.viewport().width()))
        if self._zoom_slider.value() != value:
            self._zoom_slider.blockSignals(True)
            self._zoom_slider.setValue(value)
            self._zoom_slider.blockSignals(False)
            self._pending_slider = value
