"""Menu bar and timeline tool controls."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QAction, QActionGroup, QKeySequence, QShortcut
from PySide6.QtWidgets import QApplication, QMessageBox, QWidget

from corrcut.config import APP_NAME
from corrcut.domain.operation import OperationType
from corrcut.io.project_store import PROJECT_SUFFIX
from corrcut.io.recent_projects import RecentProjectEntry, list_recent_projects
from corrcut.notices import about_text
from corrcut.ui.timeline.timeline_mode import TimelineMode

_KEY_SNAP_TO_SCENE_CUTS = "timeline/snap_to_scene_cuts"


def _recent_project_menu_label(entry: RecentProjectEntry) -> str:
    name = entry.path.stem if entry.path.suffix.lower() == PROJECT_SUFFIX else entry.path.name
    label = name.replace("&", "&&")
    if entry.exists:
        return label
    return f"{label} (missing)"


class EditorMenusMixin:
    def _add_menu_action(
        self,
        menu,
        text: str,
        slot,
        *,
        shortcut: QKeySequence | None = None,
    ) -> QAction:
        act = QAction(text, self)
        if shortcut is not None:
            act.setShortcut(shortcut)
        act.triggered.connect(slot)
        menu.addAction(act)
        return act

    def _build_menus(self) -> None:
        file_menu = self.menuBar().addMenu("&File")
        self._add_menu_action(
            file_menu,
            "&New project",
            self._new_project,
            shortcut=QKeySequence.StandardKey.New,
        )
        self._add_menu_action(
            file_menu,
            "Open &media…",
            self._open_media,
            shortcut=QKeySequence("Ctrl+O"),
        )
        self._add_menu_action(
            file_menu,
            "Open pro&ject…",
            self._open_project,
            shortcut=QKeySequence("Ctrl+Shift+O"),
        )
        self._recent_projects_menu = file_menu.addMenu("Open &recent")
        self._recent_projects_menu.aboutToShow.connect(self._populate_recent_projects_menu)
        import_menu = file_menu.addMenu("&Import")
        self._add_menu_action(
            import_menu,
            "&EDL…",
            self._import_edl,
            shortcut=QKeySequence("Ctrl+Alt+E"),
        )
        self._add_menu_action(
            import_menu,
            "S&KP…",
            self._import_skp,
            shortcut=QKeySequence("Ctrl+Alt+K"),
        )
        self._add_menu_action(
            import_menu,
            "&corr.json…",
            self._open_corr_json,
            shortcut=QKeySequence("Ctrl+E"),
        )
        export_menu = file_menu.addMenu("&Export")
        self._add_menu_action(
            export_menu,
            "&corr.json",
            self._save_corr_json,
            shortcut=QKeySequence("Ctrl+Alt+S"),
        )
        self._add_menu_action(
            export_menu,
            "corr.json &as…",
            self._save_corr_json_as,
            shortcut=QKeySequence("Ctrl+Alt+Shift+S"),
        )
        self._relink_action = self._add_menu_action(
            file_menu,
            "Relink &media…",
            self._relink_media,
        )
        self._relink_action.setEnabled(False)
        self._add_menu_action(
            file_menu,
            "&Close project",
            self._close_project,
            shortcut=QKeySequence.StandardKey.Close,
        )
        file_menu.addSeparator()
        self._add_menu_action(
            file_menu,
            "Save &project",
            self._save_project,
            shortcut=QKeySequence.StandardKey.Save,
        )
        self._add_menu_action(
            file_menu,
            "Save project as…",
            self._save_project_as,
            shortcut=QKeySequence.StandardKey.SaveAs,
        )
        file_menu.addSeparator()
        self._add_menu_action(
            file_menu,
            "E&xit",
            self._exit_application,
            shortcut=QKeySequence.StandardKey.Quit,
        )

        self._build_edit_menu()
        self._build_playback_menu()
        self._build_view_menu()
        self._init_scene_cuts_toggle()
        self._build_subtitles_menu()
        self._build_ai_menu()
        self._build_help_menu()
        self._build_timeline_zoom_shortcuts()

    def _build_help_menu(self) -> None:
        help_menu = self.menuBar().addMenu("&Help")
        self._add_menu_action(help_menu, f"&About {APP_NAME}", self._show_about)

    def _show_about(self) -> None:
        parent = self if isinstance(self, QWidget) else None
        QMessageBox.about(parent, f"About {APP_NAME}", about_text())

    def _populate_recent_projects_menu(self) -> None:
        menu = self._recent_projects_menu
        menu.clear()
        entries = list_recent_projects()
        if not entries:
            empty = QAction("No recent projects", self)
            empty.setEnabled(False)
            menu.addAction(empty)
            return
        for entry in entries:
            label = _recent_project_menu_label(entry)
            act = QAction(label, self)
            act.setToolTip(str(entry.path))
            act.setStatusTip(str(entry.path))
            path = entry.path
            act.triggered.connect(
                lambda _checked=False, target=path: self._open_recent_project(target)
            )
            menu.addAction(act)

    def _build_view_menu(self) -> None:
        view_menu = self.menuBar().addMenu("&View")
        for dock, label in (
            (self._ops_dock, "&Operations"),
            (self._track_dock, "&Streams"),
            (self._suggestions_dock, "&AI Suggestions"),
            (self._subtitles_dock, "S&ubtitles"),
            (self._timeline_dock, "&Timeline"),
        ):
            act = dock.toggleViewAction()
            act.setText(label)
            view_menu.addAction(act)
        view_menu.addSeparator()
        self._preserve_letterbox_action = QAction("Preserve &letterbox on zoom", self)
        self._preserve_letterbox_action.setCheckable(True)
        self._preserve_letterbox_action.setChecked(True)
        self._preserve_letterbox_action.setToolTip(
            "Keep top/bottom black bars under Zoom. "
            "Turn off to reframe and fill the frame."
        )
        self._preserve_letterbox_action.toggled.connect(self._on_preserve_letterbox_toggled)
        view_menu.addAction(self._preserve_letterbox_action)

    def _sync_preserve_letterbox_action(self) -> None:
        action = getattr(self, "_preserve_letterbox_action", None)
        if action is None:
            return
        enabled = True
        if self._project is not None:
            enabled = bool(self._project.preserve_letterbox_on_zoom)
        action.blockSignals(True)
        action.setChecked(enabled)
        action.blockSignals(False)

    def _on_preserve_letterbox_toggled(self, enabled: bool) -> None:
        if self._project is None:
            return
        self._project.preserve_letterbox_on_zoom = bool(enabled)
        self._notify_project_modified()
        self._sync_preview_filters()
        if enabled:
            self._schedule_deferred(0, lambda: self._force_video_matte_refresh())

    def _force_video_matte_refresh(self) -> None:
        if self._ensure_video_matte(force=True):
            if self._project is not None and self._project.selected_track() is not None:
                self._preview_panel.sync_track_and_filters()

    def _init_scene_cuts_toggle(self) -> None:
        settings = self._window_settings()
        snap_cuts = settings.value(_KEY_SNAP_TO_SCENE_CUTS, True)
        enabled = True if snap_cuts is None else bool(snap_cuts)
        if isinstance(snap_cuts, str):
            enabled = snap_cuts.strip().lower() not in {"0", "false", "no"}
        self._timeline_panel.set_scene_cuts_snap_enabled(enabled)
        self._timeline_panel.scene_cuts_snap_changed.connect(self._on_scene_cuts_from_panel)

    def _on_scene_cuts_from_panel(self, enabled: bool) -> None:
        self._window_settings().setValue(_KEY_SNAP_TO_SCENE_CUTS, bool(enabled))

    def _build_edit_menu(self) -> None:

        edit_menu = self.menuBar().addMenu("&Edit")
        self._undo_action = self._add_menu_action(
            edit_menu,
            "&Undo",
            self._undo,
            shortcut=QKeySequence.StandardKey.Undo,
        )
        self._redo_action = self._add_menu_action(
            edit_menu,
            "&Redo",
            self._redo,
            shortcut=QKeySequence.StandardKey.Redo,
        )
        self._undo_action.setEnabled(False)
        self._redo_action.setEnabled(False)
        edit_menu.addSeparator()
        self._timeline_tool_actions: list[QAction] = []
        for label, slot, shortcut in (
            ("&Deselect", self._apply_select_tool, "V"),
            ("&Mute", lambda: self._apply_edit_type(OperationType.MUTE), "M"),
            ("&Volume", lambda: self._apply_edit_type(OperationType.VOLUME), None),
            ("&Beep", lambda: self._apply_edit_type(OperationType.BEEP), "B"),
            ("&Skip", lambda: self._apply_edit_type(OperationType.SKIP), None),
            ("Blan&k", lambda: self._apply_edit_type(OperationType.BLANK), None),
            ("&Cover", lambda: self._apply_edit_type(OperationType.COVER), None),
            ("B&lur", lambda: self._apply_edit_type(OperationType.BLUR), None),
            ("&Pixelate", lambda: self._apply_edit_type(OperationType.PIXELATE), None),
            ("&Zoom", lambda: self._apply_edit_type(OperationType.ZOOM), None),
            ("Cro&p", lambda: self._apply_edit_type(OperationType.CROP), None),
        ):
            self._timeline_tool_actions.append(
                self._add_menu_action(edit_menu, label, slot, shortcut=shortcut)
            )
        edit_menu.addSeparator()
        self._add_menu_action(
            edit_menu,
            "Toggle &enabled",
            self._toggle_selected_operation_enabled,
            shortcut="D",
        )
        self._add_menu_action(
            edit_menu,
            "&Delete selected operation",
            self._delete_selected_operation,
            shortcut=QKeySequence.StandardKey.Delete,
        )
        flags_menu = edit_menu.addMenu("&Flags")
        self._add_menu_action(
            flags_menu,
            "Drop flag &1 (Numpad 1)",
            lambda: self._drop_flag(1),
        )
        self._add_menu_action(
            flags_menu,
            "Drop flag &2 (Numpad 2)",
            lambda: self._drop_flag(2),
        )
        self._add_menu_action(
            flags_menu,
            "Drop flag &3 (Numpad 3)",
            lambda: self._drop_flag(3),
        )
        flags_menu.addSeparator()
        self._add_menu_action(
            flags_menu,
            "Flag &colors and meanings…",
            self._open_flag_colors_dialog,
        )
        self._cancel_job_action = self._add_menu_action(
            edit_menu,
            "Ca&ncel background job",
            self._cancel_background_jobs,
        )
        self._cancel_job_action.setEnabled(False)

    def _build_subtitles_menu(self) -> None:
        subs_menu = self.menuBar().addMenu("&Subtitles")
        self._add_menu_action(
            subs_menu,
            "&Refresh from source",
            self._on_subtitles_refresh_requested,
        )
        self._add_menu_action(
            subs_menu,
            "&Generate from Whisper…",
            self._on_subtitles_generate_requested,
        )
        subs_menu.addSeparator()
        self._add_menu_action(
            subs_menu,
            "Reapply OCR c&leanup",
            self._on_subtitles_reapply_ocr_cleanup,
        )
        subs_menu.addSeparator()
        self._add_menu_action(
            subs_menu,
            "&Add cue at playhead",
            self._on_subtitle_add_cue_requested,
        )
        self._add_menu_action(
            subs_menu,
            "&Delete selected cue",
            self._delete_selected_subtitle_cue,
        )
        subs_menu.addSeparator()
        self._add_menu_action(
            subs_menu,
            "Show &Subtitles panel",
            lambda: (self._subtitles_dock.show(), self._subtitles_dock.raise_()),
        )

    def _build_ai_menu(self) -> None:
        ai_menu = self.menuBar().addMenu("&AI")
        self._add_menu_action(
            ai_menu,
            "Generate profanity &suggestions…",
            self._generate_suggestions_from_menu,
            shortcut=QKeySequence("Ctrl+Shift+G"),
        )

    def _build_playback_menu(self) -> None:
        from PySide6.QtCore import Qt

        playback = self.menuBar().addMenu("&Playback")
        act_play = self._add_menu_action(
            playback,
            "Play / &Pause",
            self._preview_toggle_play,
            shortcut=Qt.Key.Key_Space,
        )
        act_stop = self._add_menu_action(
            playback,
            "&Stop",
            self._preview_stop,
            shortcut="K",
        )
        act_stop.setStatusTip("Pause and return to 1×. Hold K and tap J or L for slow motion.")
        playback.addSeparator()
        act_faster = self._add_menu_action(
            playback,
            "Fas&ter",
            lambda: self._shuttle_preview(1),
            shortcut="L",
        )
        act_backward = self._add_menu_action(
            playback,
            "&Backward",
            lambda: self._shuttle_preview(-1),
            shortcut="J",
        )
        # Keys are handled by the JKL event filter so K can be held for slow
        # motion. The shortcut stays visible, but it is parented to a widget
        # that never takes focus so the QAction does not fire a second time.
        hint = QWidget(self)
        hint.setObjectName("shuttleKeyHint")
        hint.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        # This widget only owns shortcut context. A visible child sits at the
        # top-left of the window and swallows clicks on File and Edit.
        hint.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        hint.setFixedSize(1, 1)
        hint.move(-1000, -1000)
        hint.hide()
        self._shuttle_key_hint = hint
        for act in (act_stop, act_faster, act_backward):
            act.setParent(hint)
            act.setShortcutContext(Qt.ShortcutContext.WidgetShortcut)
        slow_forward = self._add_menu_action(
            playback,
            "Slow for&ward (K+L)",
            lambda: self._shuttle_preview(1, slow=True),
        )
        slow_forward.setStatusTip("Hold K and tap L")
        slow_reverse = self._add_menu_action(
            playback,
            "Slow re&verse (K+J)",
            lambda: self._shuttle_preview(-1, slow=True),
        )
        slow_reverse.setStatusTip("Hold K and tap J")
        speed = playback.addMenu("Spee&d")
        self._preview_panel.attach_speed_menu(speed)
        playback.addSeparator()
        act_back = self._add_menu_action(
            playback,
            "Previous &frame",
            lambda: self._on_left_right_key(-1),
            shortcut=Qt.Key.Key_Left,
        )
        act_fwd = self._add_menu_action(
            playback,
            "Next f&rame",
            lambda: self._on_left_right_key(1),
            shortcut=Qt.Key.Key_Right,
        )
        act_start = self._add_menu_action(
            playback,
            "Go to &start",
            self._go_to_project_start,
            shortcut=Qt.Key.Key_Home,
        )
        act_end = self._add_menu_action(
            playback,
            "Go to &end",
            self._go_to_project_end,
            shortcut=Qt.Key.Key_End,
        )
        playback.addSeparator()
        self._add_menu_action(
            playback,
            "&Fullscreen preview",
            self._preview_toggle_fullscreen,
            shortcut=QKeySequence("Alt+Return"),
        )
        playback.addSeparator()
        self._add_menu_action(
            playback,
            "Toggle &edited",
            self._toggle_preview_edits,
            shortcut="A",
        )
        self._add_menu_action(
            playback,
            "Playback &profile…",
            self._preview_panel.open_playback_profile,
        )
        playback.addSeparator()

        sound_output = playback.addMenu("Sound &Output")
        sound_output.setToolTipsVisible(True)
        sound_output.aboutToShow.connect(lambda: self._populate_sound_output_menu(sound_output))

        playback.addSeparator()
        self._add_menu_action(
            playback,
            "Refresh audio output devices",
            self._preview_panel.refresh_audio_devices,
        )
        self._playback_actions = (act_play, act_stop, act_back, act_fwd, act_start, act_end)

    def _shuttle_preview(self, direction: int, *, slow: bool = False) -> None:
        if self._focus_blocks_edit_shortcut():
            return
        self._preview_panel.shuttle(direction, slow=slow)

    def _populate_sound_output_menu(self, menu) -> None:  # type: ignore[no-untyped-def]
        menu.clear()
        rows, current = self._preview_panel.audio_output_devices()
        if not rows:
            disabled = QAction("(no devices)", self)
            disabled.setEnabled(False)
            menu.addAction(disabled)
            return

        group = QActionGroup(self)
        group.setExclusive(True)

        for name, desc in rows:
            act = QAction(desc, self)
            act.setCheckable(True)
            act.setChecked(current is not None and name == current)
            act.setToolTip(name)
            group.addAction(act)
            act.triggered.connect(
                lambda _checked=False, n=name: self._preview_panel.set_audio_output_device(n)
            )
            menu.addAction(act)

    def _build_timeline_zoom_shortcuts(self) -> None:
        for seq in ("Ctrl++", "Ctrl+="):
            shortcut = QShortcut(QKeySequence(seq), self)
            shortcut.activated.connect(self._timeline_panel.zoom_in)
        zoom_out = QShortcut(QKeySequence("Ctrl+-"), self)
        zoom_out.activated.connect(self._timeline_panel.zoom_out)

    def _on_tool_strip_mode(self, mode: TimelineMode) -> None:
        op_type = mode.create_operation_type
        if op_type is None:
            self._apply_select_tool()
            return
        self._apply_edit_type(op_type)

    def _sync_timeline_mode_combo(self, mode: TimelineMode) -> None:
        self._timeline_panel.set_tool_mode(mode)

    def _set_timeline_mode(self, mode: TimelineMode) -> None:
        self._timeline.set_timeline_mode(TimelineMode.SELECT)
        op_type = mode.create_operation_type
        if op_type is not None:
            self._apply_edit_type(op_type)
            return
        self._apply_select_tool()

    def _on_timeline_mode_from_widget(self, mode_name: str) -> None:
        try:
            mode = TimelineMode(mode_name)
        except ValueError:
            return
        self._sync_timeline_mode_combo(mode)

    def _sync_draw_type_combo(self, name: str) -> None:
        del name

    def _on_left_right_key(self, direction: int) -> None:
        from PySide6.QtWidgets import QLineEdit

        if isinstance(QApplication.focusWidget(), QLineEdit):
            return
        mods = QApplication.keyboardModifiers()
        if self._timeline.nudge_selected_region(
            direction,
            fine=bool(mods & Qt.KeyboardModifier.ShiftModifier),
            trim_in=bool(mods & Qt.KeyboardModifier.AltModifier),
            trim_out=bool(mods & Qt.KeyboardModifier.ControlModifier),
        ):
            return
        self._preview_panel.frame_step(direction)

    def _delete_selected_operation(self) -> None:
        if getattr(self, "_spatial_selection", "region") == "keyframe":
            self._try_remove_selected_keyframe()
            return
        flag_id = self._timeline.selected_flag_id()
        if flag_id is not None:
            self._delete_flag(flag_id)
            return
        suggestion_id = self._timeline.selected_suggestion_id()
        if suggestion_id is not None:
            self._reject_suggestion(suggestion_id)
            return
        op_id = self._timeline.selected_operation_id()
        if op_id is not None:
            self._delete_operation(op_id)

    def _toggle_selected_operation_enabled(self) -> None:
        if self._focus_blocks_edit_shortcut():
            return
        op_id = self._timeline.selected_operation_id()
        if op_id is None:
            op_id = self._operations_panel.current_operation_id()
        if op_id is not None:
            self._operations_panel.toggle_enabled(op_id)
