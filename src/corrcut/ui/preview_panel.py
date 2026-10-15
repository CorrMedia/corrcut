"""Central preview: libmpv video + FiltergraphCompiler lavfi (Phase 3)."""

from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtCore import QEvent, QObject, QPoint, QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import (
    QAction,
    QColor,
    QFontMetrics,
    QKeyEvent,
    QKeySequence,
    QMouseEvent,
    QPainter,
    QPen,
    QShortcut,
    QWindow,
)
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QHBoxLayout,
    QLabel,
    QMenu,
    QPushButton,
    QSlider,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from corrcut.domain.operation import OperationType
from corrcut.domain.project import Project
from corrcut.engine.subtitles.display import SubtitleCueCursor
from corrcut.preview.lavfi_mpv import (
    MPV_PASSTHROUGH,
    PREVIEW_VIDEO_WINDOW_RADIUS_S,
    compile_playback_lavfi,
)
from corrcut.preview.mpv_backend import (
    MPV_VOLUME_MAX,
    MpvEmbedWidget,
    MpvUnavailableWidget,
    is_mpv_available,
)
from corrcut.preview.playback_profile import PlaybackProfile, save_playback_profile
from corrcut.preview.playback_speed import (
    FORWARD_RATES,
    REVERSE_RATES,
    format_playback_rate,
    shuttle_target,
)
from corrcut.preview.skip_playback import (
    skip_exit_seconds,
    skip_ranges_from_project,
    skip_rewind_seconds,
)
from corrcut.ui.buttons import use_pill_button
from corrcut.ui.flag_slots import flag_slot_for_key
from corrcut.ui.flag_toast import FlagDropToast
from corrcut.ui.keyframe_timeline import KeyframeTimeline
from corrcut.ui.playback_profile_popup import PlaybackProfilePopup
from corrcut.ui.subtitle_overlay import (
    SubtitleOverlay,
    floating_window_flags,
)
from corrcut.ui.transport_controls import (
    TRANSPORT_BUTTON_STYLE,
    frame_back_icon,
    frame_fwd_icon,
    fullscreen_enter_icon,
    fullscreen_exit_icon,
    make_transport_button,
    pause_icon,
    play_icon,
    seek_end_icon,
    seek_start_icon,
    volume_icon,
)
from corrcut.ui.video_region_overlay import VideoRegionOverlay

# Hold lavfi graph swaps during a drag so mpv is not reconfigured on every move.
LAVFI_DRAG_DEBOUNCE_MS = 80


class _MovieMark(QWidget):
    """Small film strip drawn for the empty preview."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFixedSize(QSize(64, 44))

    def paintEvent(self, _event: QEvent) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        color = QColor("#8f8f8f")
        pen = QPen(color)
        pen.setWidthF(1.5)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        # Celluloid body, with the picture window inset so the edges read as rails.
        painter.drawRoundedRect(QRectF(1.75, 1.75, 60.5, 40.5), 2.5, 2.5)
        painter.drawRoundedRect(QRectF(13, 5.5, 38, 33), 1.5, 1.5)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(color)
        for top in (9.75, 19.75, 29.75):
            painter.drawRoundedRect(QRectF(4.75, top, 4.5, 4.5), 1, 1)
            painter.drawRoundedRect(QRectF(54.75, top, 4.5, 4.5), 1, 1)


class _EmptyPreview(QWidget):
    """Centered prompt shown until a movie is loaded."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("emptyPreview")
        self.setStyleSheet(
            "#emptyPreview { background-color: black; }"
            "QLabel#emptyPreviewTitle {"
            " color: #e8e8e8;"
            " background: transparent;"
            " font-size: 18px;"
            " font-weight: 600;"
            "}"
        )
        # QWidget subclasses ignore stylesheet backgrounds unless they paint them.
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        column = QVBoxLayout(self)
        column.setContentsMargins(24, 24, 24, 24)
        column.setSpacing(14)
        column.addStretch(1)
        column.addWidget(_MovieMark(), alignment=Qt.AlignmentFlag.AlignHCenter)
        self.title = QLabel("Open a movie")
        self.title.setObjectName("emptyPreviewTitle")
        self.title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        column.addWidget(self.title)
        self.drop_hint = QLabel("or drop a media or project file")
        self.drop_hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.drop_hint.setStyleSheet("color: #8f8f8f; background: transparent; font-size: 13px;")
        column.addWidget(self.drop_hint)
        self.open_button = QPushButton("Open media")
        use_pill_button(self.open_button, primary=True)
        self.open_project_button = QPushButton("Open project")
        use_pill_button(self.open_project_button)
        actions = QHBoxLayout()
        actions.setSpacing(8)
        actions.addStretch(1)
        actions.addWidget(self.open_button)
        actions.addWidget(self.open_project_button)
        actions.addStretch(1)
        column.addLayout(actions)
        column.addStretch(1)

    def paintEvent(self, event: QEvent) -> None:  # noqa: N802
        del event
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor(0, 0, 0))


def _keep_hidden_video_size(widget: QWidget) -> None:
    policy = widget.sizePolicy()
    policy.setRetainSizeWhenHidden(True)
    widget.setSizePolicy(policy)


def _mpv_embed_widget(widget: QWidget) -> MpvEmbedWidget | None:
    return widget if isinstance(widget, MpvEmbedWidget) else None


def _configure_native_overlay(widget: QWidget) -> None:
    """Give overlay controls their own native window so they stack above mpv embed."""
    widget.setAttribute(Qt.WA_NativeWindow, True)
    widget.setAttribute(Qt.WA_DontCreateNativeAncestors, True)


def _stack_native_above(widget: QWidget) -> None:
    """Force a native overlay HWND to the top of the sibling z-order (above mpv)."""
    if not widget.isVisible():
        return
    widget.raise_()
    # Ensure the native window exists before reordering.
    widget_hwnd = int(widget.winId())
    if sys.platform != "win32":
        return
    import ctypes

    user32 = ctypes.windll.user32
    HWND_TOP = 0
    SWP_NOSIZE = 0x0001
    SWP_NOMOVE = 0x0002
    SWP_NOACTIVATE = 0x0010
    SWP_SHOWWINDOW = 0x0040
    user32.SetWindowPos(
        widget_hwnd,
        HWND_TOP,
        0,
        0,
        0,
        0,
        SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE | SWP_SHOWWINDOW,
    )


class _VideoOverlayHost(QWidget):
    """Video area with a child widget pinned to the bottom-right corner."""

    _OVERLAY_MARGIN = 10

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._overlay: QWidget | None = None
        self.on_geometry_changed = None
        self._content_layout = QVBoxLayout(self)
        self._content_layout.setContentsMargins(0, 0, 0, 0)
        self.setObjectName("videoHost")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet("#videoHost { background-color: black; }")

    def paintEvent(self, event: QEvent) -> None:  # noqa: N802
        del event
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor(0, 0, 0))

    def content_layout(self) -> QVBoxLayout:
        return self._content_layout

    def set_overlay(self, widget: QWidget) -> None:
        self._overlay = widget
        _configure_native_overlay(widget)
        widget.setParent(self)
        self.raise_overlay()

    def raise_overlay(self) -> None:
        self._position_overlay()

    def resizeEvent(self, event: QEvent) -> None:  # noqa: N802
        super().resizeEvent(event)
        self.raise_overlay()
        self._notify_geometry()

    def showEvent(self, event: QEvent) -> None:  # noqa: N802
        super().showEvent(event)
        QTimer.singleShot(0, self.raise_overlay)
        QTimer.singleShot(0, self._notify_geometry)

    def _notify_geometry(self) -> None:
        callback = self.on_geometry_changed
        if callback is not None:
            callback()

    def _lower_content(self) -> None:
        for index in range(self._content_layout.count()):
            item = self._content_layout.itemAt(index)
            if item is None:
                continue
            widget = item.widget()
            if widget is not None:
                widget.lower()

    def _position_overlay(self) -> None:
        if self._overlay is None:
            return
        self._lower_content()
        margin = self._OVERLAY_MARGIN
        self._overlay.move(
            max(0, self.width() - self._overlay.width() - margin),
            max(0, self.height() - self._overlay.height() - margin),
        )
        self._overlay.raise_()
        self._overlay.show()


class _PreviewFullscreenWindow(QWidget):
    """Borderless fullscreen host for the mpv embed; Esc exits playback fullscreen."""

    _CURSOR_HIDE_MS = 2500

    def __init__(self, preview: PreviewPanel) -> None:
        super().__init__(preview, Qt.WindowType.Window)
        self._preview = preview
        self.setWindowTitle("Preview")
        self.setStyleSheet("background-color: black;")
        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._cursor_hidden = False
        self._event_filter_installed = False
        self._hide_cursor_timer = QTimer(self)
        self._hide_cursor_timer.setSingleShot(True)
        self._hide_cursor_timer.timeout.connect(self._hide_cursor)
        self.setMouseTracking(True)
        self._esc_shortcut = QShortcut(Qt.Key.Key_Escape, self)
        self._esc_shortcut.setContext(Qt.ShortcutContext.ApplicationShortcut)
        self._esc_shortcut.activated.connect(self._preview.exit_fullscreen)
        self._space_shortcut = QShortcut(Qt.Key.Key_Space, self)
        self._space_shortcut.setContext(Qt.ShortcutContext.ApplicationShortcut)
        self._space_shortcut.activated.connect(self._preview.toggle_playback)
        for sequence in (QKeySequence("Alt+Return"), QKeySequence("Alt+Enter")):
            shortcut = QShortcut(sequence, self)
            shortcut.setContext(Qt.ShortcutContext.ApplicationShortcut)
            shortcut.activated.connect(self._preview.toggle_fullscreen)

    def _emit_flag(self, slot: int | None) -> None:
        if slot is None:
            return
        self._preview.flag_requested.emit(slot)

    def showEvent(self, event: QEvent) -> None:  # noqa: N802
        super().showEvent(event)
        app = QApplication.instance()
        if app is not None and not self._event_filter_installed:
            app.installEventFilter(self)
            self._event_filter_installed = True
        self._show_cursor()

    def hideEvent(self, event: QEvent) -> None:  # noqa: N802
        self._teardown_cursor_autohide()
        super().hideEvent(event)

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:  # noqa: N802
        if self.isVisible() and event.type() == QEvent.Type.KeyPress and isinstance(event, QKeyEvent):
            if not event.isAutoRepeat() and self._flag_key_is_for_window(watched):
                slot = flag_slot_for_key(event.key(), event.modifiers(), digits=True)
                if slot is not None:
                    self._emit_flag(slot)
                    return True
        if (
            self.isVisible()
            and event.type() == QEvent.Type.MouseMove
            and isinstance(event, QMouseEvent)
            and self._pointer_over_window(event.globalPosition().toPoint())
        ):
            self._on_pointer_activity()
        return super().eventFilter(watched, event)

    def _flag_key_is_for_window(self, watched: QObject) -> bool:
        if QApplication.activeWindow() is self:
            return True
        if watched is self:
            return True
        return isinstance(watched, QWidget) and self.isAncestorOf(watched)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        self._on_pointer_activity()
        super().mouseMoveEvent(event)

    def restore_cursor(self) -> None:
        """Show the cursor and stop auto-hide (call when leaving fullscreen)."""
        self._teardown_cursor_autohide()

    def _pointer_over_window(self, global_pos) -> bool:  # type: ignore[no-untyped-def]
        return self.frameGeometry().contains(global_pos)

    def _on_pointer_activity(self) -> None:
        self._show_cursor()

    def _show_cursor(self) -> None:
        if self._cursor_hidden:
            while QApplication.overrideCursor() is not None:
                QApplication.restoreOverrideCursor()
            self._cursor_hidden = False
        self._preview._set_fullscreen_controls_visible(True)
        self._hide_cursor_timer.start(self._CURSOR_HIDE_MS)

    def _hide_cursor(self) -> None:
        if not self.isVisible() or self._cursor_hidden:
            return
        QApplication.setOverrideCursor(Qt.CursorShape.BlankCursor)
        self._cursor_hidden = True
        self._preview._set_fullscreen_controls_visible(False)

    def _teardown_cursor_autohide(self) -> None:
        self._hide_cursor_timer.stop()
        app = QApplication.instance()
        if app is not None and self._event_filter_installed:
            app.removeEventFilter(self)
            self._event_filter_installed = False
        if self._cursor_hidden:
            while QApplication.overrideCursor() is not None:
                QApplication.restoreOverrideCursor()
            self._cursor_hidden = False
        self._preview._set_fullscreen_controls_visible(True)

    def keyPressEvent(self, event: QKeyEvent) -> None:  # noqa: N802
        if event.key() == Qt.Key.Key_Escape:
            self._preview.exit_fullscreen()
            event.accept()
            return
        if event.key() == Qt.Key.Key_Space and not event.isAutoRepeat():
            self._preview.toggle_playback()
            event.accept()
            return
        if (
            event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter)
            and event.modifiers() & Qt.KeyboardModifier.AltModifier
        ):
            self._preview.toggle_fullscreen()
            event.accept()
            return
        slot = flag_slot_for_key(event.key(), event.modifiers(), digits=True)
        if slot is not None and not event.isAutoRepeat():
            self._preview.flag_requested.emit(slot)
            event.accept()
            return
        super().keyPressEvent(event)

    def closeEvent(self, event: QEvent) -> None:  # noqa: N802
        event.ignore()
        self._preview.exit_fullscreen()

    def resizeEvent(self, event: QEvent) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._preview._position_fullscreen_button(self)
        self._preview._reposition_subtitle_overlay()
        self._preview._schedule_subtitle_relayout()
        self._preview._position_original_mark()


class _CenteredControlBar(QWidget):
    """Pin side clusters to the edges and keep the transport on the midline.

    The side clusters are different widths, so equal stretches would leave the
    transport off center. This bar places it on the midline and slides it
    aside only when it would overlap a side cluster.
    """

    _MARGIN = 8
    _GAP = 12

    def __init__(
        self,
        left: QWidget,
        center: QWidget,
        right: QWidget,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._left = left
        self._center = center
        self._right = right
        left.setParent(self)
        center.setParent(self)
        right.setParent(self)

    def sizeHint(self) -> QSize:  # noqa: N802
        return self._hint(self._left.sizeHint(), self._center.sizeHint(), self._right.sizeHint())

    def minimumSizeHint(self) -> QSize:  # noqa: N802
        return self._hint(
            self._left.minimumSizeHint(),
            self._center.minimumSizeHint(),
            self._right.minimumSizeHint(),
        )

    def reposition(self) -> None:
        width = self.width()
        height = self.height()
        if width <= 0 or height <= 0:
            return
        left_size = self._left.sizeHint()
        center_size = self._center.sizeHint()
        right_size = self._right.sizeHint()
        left_x = self._MARGIN
        right_x = max(self._MARGIN, width - self._MARGIN - right_size.width())
        center_x = (width - center_size.width()) // 2
        left_limit = left_x + left_size.width() + self._GAP
        right_limit = right_x - self._GAP - center_size.width()
        if left_limit <= right_limit:
            center_x = min(max(center_x, left_limit), right_limit)
        else:
            center_x = left_limit
        self._place(self._left, left_x, left_size, height)
        self._place(self._center, center_x, center_size, height)
        self._place(self._right, right_x, right_size, height)

    def resizeEvent(self, event: QEvent) -> None:  # noqa: N802
        super().resizeEvent(event)
        self.reposition()

    def showEvent(self, event: QEvent) -> None:  # noqa: N802
        super().showEvent(event)
        self.reposition()

    def _place(self, widget: QWidget, x: int, size: QSize, height: int) -> None:
        widget.setGeometry(x, max(0, (height - size.height()) // 2), size.width(), size.height())

    def _hint(self, left: QSize, center: QSize, right: QSize) -> QSize:
        return QSize(
            left.width() + center.width() + right.width() + self._GAP * 2 + self._MARGIN * 2,
            max(left.height(), center.height(), right.height()) + self._MARGIN * 2,
        )


class PreviewPanel(QWidget):
    """Video preview with transport and original/edited A/B monitoring."""

    position_changed = Signal(float)
    playback_state_changed = Signal(bool)
    fullscreen_changed = Signal(bool)
    subtitle_cue_edited = Signal(str, str)
    subtitle_edit_started = Signal()
    video_region_box_changed = Signal(dict)
    video_region_endpoint_changed = Signal(str)
    video_region_keyframe_index_changed = Signal(int)
    video_region_add_keyframe_requested = Signal()
    video_region_add_keyframe_at_requested = Signal(float)
    video_region_remove_keyframe_requested = Signal()
    video_region_key_moved = Signal(int, float)
    keyframe_track_seek_requested = Signal(float)
    keyframe_track_seek_finished = Signal(float)
    keyframe_track_background_pressed = Signal()
    about_to_play = Signal()
    playback_filter_changed = Signal()
    flag_requested = Signal(int)
    open_media_requested = Signal()
    open_project_requested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._project: Project | None = None
        self._edits_enabled = True
        self._playback_profile = PlaybackProfile.full()
        self._profile_popup: PlaybackProfilePopup | None = None
        self._subtitles_visible = True
        self._media_path: Path | None = None
        self._load_ready_handler = None
        self._tracks_ready_handler = None
        self._media_load_completed = False
        self._sync_generation = 0
        self._media_setup_generation: int | None = None
        self._load_settle_generation: int | None = None
        self._load_settle_attempt = 0
        self._start_drift_suppress_timer = QTimer(self)
        self._start_drift_suppress_timer.setSingleShot(True)
        self._start_drift_suppress_timer.setInterval(2500)
        self._pending_edits_lavfi = False
        self._last_lavfi_graph: str | None = None
        self._last_lavfi_aid: int | None = None
        # Playhead-centered video effect window (crop/scale blur is not enable-gated).
        self._lavfi_video_window: tuple[float, float] | None = None
        self._lavfi_window_radius_s = PREVIEW_VIDEO_WINDOW_RADIUS_S
        # Rebuild before the playhead reaches the compiled window edge.
        self._lavfi_window_rebuild_margin_s = max(4.0, PREVIEW_VIDEO_WINDOW_RADIUS_S * 0.4)
        # User seeks (op click / scrub) while mpv is still opening — apply after settle.
        self._pending_seek_s: float | None = None
        self._pending_seek_reference: str = "absolute+exact"
        self._is_playing = False
        # Skip jumps apply only after Play. mpv frame-step clears pause for one
        # frame, and a time-pos tick in that window would seek to the skip end.
        self._skip_jumps_enabled = False
        self._playback_rate = 1.0
        self._speed_actions: list[tuple[QAction, float]] = []
        self._apply_zoom_preview = True
        self._lavfi_apply_timer = QTimer(self)
        self._lavfi_apply_timer.setSingleShot(True)
        self._lavfi_apply_timer.timeout.connect(self._flush_lavfi_apply)
        self._lavfi_reload_delay_ms = 0
        self._seek_token = 0
        self._exact_seek_timer = QTimer(self)
        self._exact_seek_timer.setSingleShot(True)
        self._exact_seek_timer.timeout.connect(self._flush_exact_seek_retry)
        self._exact_seek_retry_s: float | None = None
        # Box-track drag: one keyframe seek in flight, exact seek on release.
        self._pending_keyframe_scrub_s: float | None = None
        self._keyframe_scrub_timer = QTimer(self)
        self._keyframe_scrub_timer.setSingleShot(True)
        self._keyframe_scrub_timer.setInterval(40)
        self._keyframe_scrub_timer.timeout.connect(self._flush_keyframe_scrub_seek)
        # After a skip jump, ignore mpv ticks still reporting times inside the skip.
        self._skip_ignore_until: float | None = None
        self._skip_ignore_reverse = False
        # Sorted enabled skips. Rebuilt when the project or an edit changes.
        self._skip_ranges: list[tuple[float, float]] = []
        self._fullscreen_window: _PreviewFullscreenWindow | None = None
        self._is_fullscreen = False
        self._overlay_anchor_handle = None
        self._overlay_stacking_embed: MpvEmbedWidget | None = None
        self._subtitle_overlay_seconds = -1.0
        self._subtitle_cursor = SubtitleCueCursor()
        self._subtitle_display_cue_id: str | None = None
        self._subtitle_display_text = ""
        self._subtitle_display_visible = False
        self._subtitle_display_editable = False

        self._play_icon = play_icon()
        self._pause_icon = pause_icon()
        self._play_btn = make_transport_button(
            self._play_icon,
            "Play / pause (Space). Keeps the current playback speed.",
            prominent=True,
        )
        self._play_btn.clicked.connect(self._toggle_playback)

        seek_start_btn = make_transport_button(
            seek_start_icon(),
            "Go to start (Home)",
        )
        seek_start_btn.clicked.connect(self.seek_to_start)

        step_back = make_transport_button(
            frame_back_icon(),
            "Previous frame",
        )
        step_back.clicked.connect(lambda: self.frame_step(-1))

        step_fwd = make_transport_button(
            frame_fwd_icon(),
            "Next frame",
        )
        step_fwd.clicked.connect(lambda: self.frame_step(1))

        seek_end_btn = make_transport_button(
            seek_end_icon(),
            "Go to end (End)",
        )
        seek_end_btn.clicked.connect(self.seek_to_end)

        self._fullscreen_enter_icon = fullscreen_enter_icon()
        self._fullscreen_exit_icon = fullscreen_exit_icon()
        self._fullscreen_btn = make_transport_button(
            self._fullscreen_enter_icon,
            "Fullscreen video (Alt+Enter to toggle, Esc to exit)",
            overlay=True,
        )
        _configure_native_overlay(self._fullscreen_btn)
        self._fullscreen_btn.clicked.connect(self.toggle_fullscreen)

        self._ab_toggle = QCheckBox("Edited")
        self._ab_toggle.setChecked(True)
        self._ab_toggle.setToolTip(
            "Edited applies the playback profile (categories and actions). "
            "Original plays the source media."
        )
        self._ab_toggle.toggled.connect(self._on_ab_toggled)

        self._profile_btn = QPushButton()
        self._profile_btn.setObjectName("playbackProfileButton")
        use_pill_button(self._profile_btn)
        self._profile_btn.setMinimumWidth(108)
        self._profile_btn.clicked.connect(self.open_playback_profile)
        self._sync_profile_button()

        self._subs_toggle = QCheckBox("Subtitles")
        self._subs_toggle.setChecked(True)
        self._subs_toggle.setToolTip("Show or hide edited subtitle overlay on the video preview")
        self._subs_toggle.toggled.connect(self._on_subs_toggled)

        self._volume_unmuted_icon = volume_icon()
        self._volume_muted_icon = volume_icon(muted=True)
        self._volume_btn = QToolButton()
        self._volume_btn.setIcon(self._volume_unmuted_icon)
        self._volume_btn.setIconSize(QSize(22, 22))
        self._volume_btn.setFixedSize(QSize(26, 26))
        self._volume_btn.setAutoRaise(True)
        self._volume_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._volume_btn.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonIconOnly)
        self._volume_btn.setStyleSheet(TRANSPORT_BUTTON_STYLE)
        self._volume_btn.setToolTip("Mute preview")
        self._volume_btn.clicked.connect(self._on_volume_icon_clicked)
        self._volume_slider = QSlider(Qt.Orientation.Horizontal)
        self._volume_slider.setRange(0, MPV_VOLUME_MAX)
        self._volume_slider.setValue(MPV_VOLUME_MAX)
        self._volume_slider.setFixedWidth(140)
        self._volume_slider.setToolTip("Preview volume (linear amplitude; 50% ≈ half loudness)")
        self._syncing_volume = False
        self._muted = False
        self._volume_before_mute = MPV_VOLUME_MAX
        self._volume_slider.valueChanged.connect(self._on_volume_changed)

        self._mpv_widget: QWidget
        self._backend = None

        self._speed_btn = QToolButton()
        self._speed_btn.setText(format_playback_rate(1.0))
        self._speed_btn.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
        self._speed_btn.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self._speed_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._speed_btn.setMinimumWidth(64)
        self._speed_btn.setToolTip(
            "Playback speed. L faster forward, J faster backward, K stops at 1×. "
            "Hold K and tap J or L for slow motion. Space pauses without changing speed."
        )
        self._speed_menu = QMenu(self._speed_btn)
        self.attach_speed_menu(self._speed_menu)
        self._speed_btn.setMenu(self._speed_menu)
        self._sync_speed_ui()

        transport = QHBoxLayout()
        transport.setSpacing(0)
        transport.setContentsMargins(0, 0, 0, 0)
        transport.addWidget(seek_start_btn)
        transport.addWidget(step_back)
        transport.addWidget(self._play_btn)
        transport.addWidget(step_fwd)
        transport.addWidget(seek_end_btn)

        center_host = QWidget()
        center = QHBoxLayout(center_host)
        center.setContentsMargins(0, 0, 0, 0)
        center.setSpacing(0)
        center.setAlignment(Qt.AlignmentFlag.AlignVCenter)
        center.addLayout(transport)
        center.addWidget(self._speed_btn)

        left_host = QWidget()
        left = QHBoxLayout(left_host)
        left.setContentsMargins(0, 0, 0, 0)
        left.setSpacing(8)
        left.setAlignment(Qt.AlignmentFlag.AlignVCenter)
        left.addWidget(self._ab_toggle)
        left.addWidget(self._profile_btn)
        left.addWidget(self._subs_toggle)

        right_host = QWidget()
        right = QHBoxLayout(right_host)
        right.setContentsMargins(0, 0, 0, 0)
        right.setSpacing(4)
        right.setAlignment(Qt.AlignmentFlag.AlignVCenter)
        right.addWidget(self._volume_btn)
        right.addWidget(self._volume_slider)

        self._controls_bar = _CenteredControlBar(left_host, center_host, right_host)
        self._controls_bar.setObjectName("previewControlsBar")
        self._controls_bar.setStyleSheet(
            "#previewControlsBar { background-color: #1a1a1a; }"
            f"{TRANSPORT_BUTTON_STYLE}"
        )

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        self._video_host = _VideoOverlayHost()
        self._video_layout_slot = self._video_host.content_layout()
        self._video_host.set_overlay(self._fullscreen_btn)
        self._empty_preview = _EmptyPreview(self._video_host)
        self._empty_hint = self._empty_preview.title
        self._empty_preview.open_button.clicked.connect(self.open_media_requested.emit)
        self._empty_preview.open_project_button.clicked.connect(self.open_project_requested.emit)
        self._empty_preview.hide()
        self._original_mark = QLabel("Original", self._video_host)
        self._original_mark.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._original_mark.setStyleSheet(
            "QLabel {"
            " color: #f2f2f2;"
            " background-color: #2a2418;"
            " font-size: 11px;"
            " font-weight: 600;"
            " padding: 3px 8px;"
            " border-left: 3px solid #e6c36a;"
            "}"
        )
        _configure_native_overlay(self._original_mark)
        self._original_mark.hide()
        self._video_host.on_geometry_changed = self._on_video_host_geometry
        self._subtitle_overlay = SubtitleOverlay(self._video_host)
        self._subtitle_overlay.setWindowFlags(floating_window_flags())
        self._subtitle_overlay.setAttribute(Qt.WA_ShowWithoutActivating, True)
        self._subtitle_overlay.setAttribute(Qt.WA_TranslucentBackground, True)
        self._subtitle_overlay.edit_committed.connect(self._on_subtitle_overlay_committed)
        self._subtitle_overlay.edit_started.connect(self._on_subtitle_overlay_edit_started)
        self._flag_toast = FlagDropToast(self._video_host)
        self._video_region_overlay = VideoRegionOverlay(self)
        self._video_region_overlay.box_changed.connect(self._on_video_region_overlay_box_changed)
        self._video_region_overlay.drag_active_changed.connect(
            lambda _active: self._sync_zoom_preview_mode()
        )
        self._video_region_overlay.endpoint_changed.connect(self.video_region_endpoint_changed.emit)
        self._video_region_overlay.keyframe_index_changed.connect(
            self.video_region_keyframe_index_changed.emit
        )
        self._video_region_overlay.add_keyframe_requested.connect(
            self.video_region_add_keyframe_requested.emit
        )
        self._video_region_overlay.remove_keyframe_requested.connect(
            self.video_region_remove_keyframe_requested.emit
        )
        self._video_region_overlay.double_clicked.connect(self._on_video_region_double_clicked)
        self._keyframe_timeline = KeyframeTimeline(self)
        self._keyframe_timeline.seek_requested.connect(self.keyframe_track_seek_requested.emit)
        self._keyframe_timeline.seek_finished.connect(self.keyframe_track_seek_finished.emit)
        self._keyframe_timeline.add_requested.connect(
            self.video_region_add_keyframe_at_requested.emit
        )
        self._keyframe_timeline.remove_requested.connect(
            self.video_region_remove_keyframe_requested.emit
        )
        self._keyframe_timeline.index_selected.connect(
            self.video_region_keyframe_index_changed.emit
        )
        self._keyframe_timeline.background_pressed.connect(
            self.keyframe_track_background_pressed.emit
        )
        self._keyframe_timeline.key_moved.connect(self.video_region_key_moved.emit)
        root.addWidget(self._video_host, stretch=1)
        root.addWidget(self._keyframe_timeline)
        root.addWidget(self._controls_bar)

        self._mount_video_widget()

    @property
    def mpv_available(self) -> bool:
        return self._backend is not None

    @property
    def is_fullscreen(self) -> bool:
        return self._is_fullscreen

    def playback_seconds(self) -> float:
        if self._backend is not None:
            try:
                return max(0.0, float(self._backend.position()))
            except Exception:
                pass
        return 0.0

    def show_flag_toast(self, color, label: str, timecode: str) -> None:  # noqa: ANN001
        host = self._subtitle_overlay_host()
        _configure_native_overlay(self._flag_toast)
        self._flag_toast.show_flag(host, color, label, timecode)
        if self._mpv_widget.parent() is host:
            self._mpv_widget.lower()
        self._flag_toast.raise_()

    def hide_flag_toast(self) -> None:
        self._flag_toast.hide()

    def toggle_fullscreen(self) -> None:
        if self._is_fullscreen:
            self.exit_fullscreen()
        else:
            self.enter_fullscreen()

    def _raise_video_overlay(self) -> None:
        if self._is_fullscreen and self._fullscreen_window is not None:
            self._position_fullscreen_button(self._fullscreen_window)
        else:
            self._video_host.raise_overlay()
        self._reposition_subtitle_overlay()
        self._reposition_video_region_overlay()
        self._stack_caption_above_region()
        if self._flag_toast.isVisible():
            self._flag_toast.raise_()
        self._position_preview_badges()

    def _sync_ab_label(self) -> None:
        if self._ab_toggle.isChecked():
            self._ab_toggle.setText("Edited")
            self._ab_toggle.setStyleSheet("")
        else:
            self._ab_toggle.setText("Original")
            self._ab_toggle.setStyleSheet("QCheckBox { color: #e6c36a; font-weight: 600; }")
        self._reposition_controls()

    def _sync_preview_guidance(self) -> None:
        self._sync_empty_hint()
        self._sync_original_mark()

    def _sync_empty_hint(self) -> None:
        widget = getattr(self, "_mpv_widget", None)
        unavailable = isinstance(widget, MpvUnavailableWidget)
        show = (
            widget is not None
            and self._media_path is None
            and not unavailable
            and not self._is_fullscreen
        )
        self._empty_preview.setVisible(show)
        if widget is not None and not unavailable and not self._is_fullscreen:
            _keep_hidden_video_size(widget)
            widget.setVisible(not show)
        if show:
            self._position_empty_hint()
        elif not self._is_fullscreen and self._fullscreen_btn.parent() is self._video_host:
            self._video_host.raise_overlay()

    def _sync_original_mark(self) -> None:
        show = self._media_path is not None and not self._edits_enabled
        host = self._subtitle_overlay_host()
        if self._original_mark.parent() is not host:
            self._original_mark.setParent(host)
            _configure_native_overlay(self._original_mark)
        self._original_mark.setVisible(show)
        if show:
            self._position_original_mark()

    def _on_video_host_geometry(self) -> None:
        self._position_preview_badges()
        if self._is_fullscreen:
            return
        self._reposition_floating_overlays()
        self._schedule_subtitle_relayout()

    def _schedule_subtitle_relayout(self) -> None:
        """Place the caption again after mpv has caught up to the new frame."""
        timer = getattr(self, "_subtitle_relayout_timer", None)
        if timer is None:
            timer = QTimer(self)
            timer.setSingleShot(True)
            timer.setInterval(120)
            timer.timeout.connect(self._reposition_subtitle_overlay)
            self._subtitle_relayout_timer = timer
        timer.start()

    def _position_preview_badges(self) -> None:
        self._position_empty_hint()
        self._position_original_mark()
        if self._media_path is None and not self._is_fullscreen:
            self._fullscreen_btn.hide()

    def _position_empty_hint(self) -> None:
        if self._empty_preview.isHidden():
            return
        host = self._video_host
        self._empty_preview.setGeometry(0, 0, host.width(), host.height())
        self._empty_preview.raise_()

    def _position_original_mark(self) -> None:
        if not self._original_mark.isVisible():
            return
        host = self._original_mark.parentWidget()
        if host is None:
            return
        mark = self._original_mark
        mark.adjustSize()
        margin = _VideoOverlayHost._OVERLAY_MARGIN
        mark.move(margin, margin)
        _stack_native_above(mark)

    def _subtitle_overlay_host(self) -> QWidget:
        if self._is_fullscreen and self._fullscreen_window is not None:
            return self._fullscreen_window
        return self._video_host

    def _subtitle_layout_reference_height(self) -> float:
        return max(1.0, float(self._video_host.height()))

    def showEvent(self, event: QEvent) -> None:  # noqa: N802
        super().showEvent(event)
        self._follow_top_level_window()
        QTimer.singleShot(0, self._follow_top_level_window)

    def event(self, event: QEvent) -> bool:  # noqa: N802
        parent_changed = event.type() == QEvent.Type.ParentChange
        result = super().event(event)
        if parent_changed:
            self._follow_top_level_window()
        return result

    def _follow_top_level_window(self) -> None:
        """Tool overlays are positioned in screen coordinates, so follow the frame."""
        window = self.window()
        handle = None if window is None else window.windowHandle()
        current = self._overlay_anchor_handle
        if handle is current:
            return
        if current is not None:
            self._detach_overlay_anchor(current)
        self._overlay_anchor_handle = handle
        if handle is None:
            return
        handle.xChanged.connect(self._reposition_floating_overlays)
        handle.yChanged.connect(self._reposition_floating_overlays)
        handle.windowStateChanged.connect(self._reposition_floating_overlays)

    def _detach_overlay_anchor(self, handle: QWindow) -> None:
        for signal in (handle.xChanged, handle.yChanged, handle.windowStateChanged):
            try:
                signal.disconnect(self._reposition_floating_overlays)
            except (TypeError, RuntimeError):
                pass

    def _reposition_floating_overlays(self, *_args: object) -> None:
        window = self.window()
        if window is None or window.isMinimized() or not window.isVisible():
            return
        if self._is_fullscreen:
            return
        self._reposition_subtitle_overlay()
        self._reposition_video_region_overlay()

    def _reposition_subtitle_overlay(self) -> None:
        host = self._subtitle_overlay_host()
        overlay = self._subtitle_overlay
        # setParent hides a tool window. Doing that on every resize shows the
        # caption again at its previous screen position, which lands on the
        # transport bar once the video host has moved.
        if overlay.parentWidget() is host and overlay.isWindow():
            overlay.reposition(
                host,
                layout_reference_height=self._subtitle_layout_reference_height(),
                content_rect=self._video_letterbox_rect(host),
            )
            self._stack_caption_above_region()
            return
        self._mount_subtitle_overlay(host)

    def _on_video_region_overlay_box_changed(self, box: dict) -> None:
        self.video_region_box_changed.emit(box)
        if not self._video_region_overlay.is_dragging:
            self._reposition_video_region_overlay()

    def _reposition_video_region_overlay(self) -> None:
        # Keep inactive overlay off the mpv surface.
        if not self._video_region_overlay.is_active:
            return
        # Freeze letterbox while the box is dragged so a stale OSD rect after a
        # seek cannot shear the normalized size mid-gesture.
        if self._video_region_overlay.is_dragging:
            return
        host = self._mpv_widget if self._mpv_widget.isVisible() else self._subtitle_overlay_host()
        content = self._video_letterbox_rect(host)
        self._video_region_overlay.layout_on_host(host, content_rect=content)
        self._stack_caption_above_region()

    def reposition_video_region_overlay(self) -> None:
        self._reposition_video_region_overlay()

    def schedule_video_region_relayout(self) -> None:
        """Re-read mpv letterbox after a seek has had time to update OSD."""
        for delay_ms in (0, 50, 150):
            QTimer.singleShot(delay_ms, self._reposition_video_region_overlay)

    def _video_letterbox_rect(self, host: QWidget) -> QRectF | None:
        """Picture area inside the embed (mpv letterbox), in host widget coords."""
        fallback = self._video_region_overlay.content_aspect
        dpr = float(host.devicePixelRatioF())
        letterbox = getattr(self._backend, "video_letterbox_rect", None)
        if callable(letterbox):
            rect = letterbox(
                float(host.width()),
                float(host.height()),
                fallback_aspect=fallback,
                device_pixel_ratio=dpr,
            )
            if rect is not None:
                x, y, w, h = rect
                return QRectF(x, y, w, h)
        from corrcut.preview.mpv_backend import aspect_letterbox_rect

        fallback_rect = aspect_letterbox_rect(
            float(host.width()), float(host.height()), fallback
        )
        if fallback_rect is None:
            return None
        x, y, w, h = fallback_rect
        return QRectF(x, y, w, h)

    def set_video_region(
        self,
        box: dict[str, float] | None,
        *,
        full_frame: bool = False,
        interactive: bool = True,
        video_width: int | None = None,
        video_height: int | None = None,
        ghost: dict[str, float] | list[dict[str, float]] | None = None,
        ghosts: list[dict[str, float]] | None = None,
        path: list[dict[str, float]] | None = None,
        endpoint: str = "start",
        keyframe_index: int | None = None,
        keyframe_count: int | None = None,
        keyframe_offsets: list[float] | None = None,
        lock_source_aspect: bool = False,
    ) -> None:
        self._video_region_overlay.set_content_aspect(video_width, video_height)
        if box is None and not full_frame:
            self._video_region_overlay.clear()
            self.clear_keyframe_track()
            self._sync_zoom_preview_mode()
            self._stack_caption_above_region()
            return
        self._video_region_overlay.set_region(
            box,
            full_frame=full_frame,
            interactive=interactive,
            ghost=ghost,
            ghosts=ghosts,
            path=path,
            endpoint=endpoint,
            keyframe_index=keyframe_index,
            keyframe_count=keyframe_count,
            keyframe_offsets=keyframe_offsets,
            lock_source_aspect=lock_source_aspect,
        )
        self._reposition_video_region_overlay()
        self._sync_zoom_preview_mode()
        if not self._video_region_overlay.is_active:
            self.clear_keyframe_track()
            self._stack_caption_above_region()

    def clear_video_region(self) -> None:
        self._video_region_overlay.clear()
        self.clear_keyframe_track()
        self._sync_zoom_preview_mode()
        self._stack_caption_above_region()

    def set_keyframe_track(
        self,
        start: float,
        end: float,
        times: list[float],
        active: int = 0,
        *,
        playhead: float | None = None,
        selected: int = -1,
    ) -> None:
        self._keyframe_timeline.set_enable_window(start, end)
        self._keyframe_timeline.set_keys(times, active, selected)
        if playhead is not None:
            self._keyframe_timeline.set_playhead(playhead)

    def set_keyframe_playhead(self, seconds: float) -> None:
        self._keyframe_timeline.set_playhead(seconds)

    def set_keyframe_active(self, index: int) -> None:
        self._keyframe_timeline.set_active(index)

    def set_keyframe_selected(self, index: int) -> None:
        self._keyframe_timeline.set_selected(index)

    def keyframe_selected_index(self) -> int:
        return self._keyframe_timeline.selected_index

    def set_video_region_keyframe_index(self, index: int) -> None:
        self._video_region_overlay.set_keyframe_index(index)

    def set_video_region_sampled_box(
        self,
        box: dict[str, float],
        ghosts: list[dict[str, float]] | None = None,
    ) -> None:
        self._video_region_overlay.set_sampled_box(box, ghosts=ghosts)

    def clear_keyframe_track(self) -> None:
        self._keyframe_timeline.clear()

    def video_region_endpoint(self) -> str:
        return self._video_region_overlay.endpoint

    def video_region_keyframe_index(self) -> int:
        return self._video_region_overlay.keyframe_index

    @property
    def is_video_region_dragging(self) -> bool:
        return self._video_region_overlay.is_dragging

    def _mount_subtitle_overlay(self, host: QWidget) -> None:
        was_visible = self._subtitle_overlay.isVisible()
        self._subtitle_overlay.setParent(host, floating_window_flags())
        self._subtitle_overlay.setAttribute(Qt.WA_ShowWithoutActivating, True)
        self._subtitle_overlay.setAttribute(Qt.WA_TranslucentBackground, True)
        if self._mpv_widget.parent() is host:
            self._mpv_widget.lower()
        self._subtitle_overlay.reposition(
            host,
            layout_reference_height=self._subtitle_layout_reference_height(),
            content_rect=self._video_letterbox_rect(host),
        )
        if was_visible:
            self._subtitle_overlay.show()
        self._stack_caption_above_region()

    def _stack_caption_above_region(self) -> None:
        """Keep caption text above the spatial effect overlay so it stays clickable."""
        if self._video_region_overlay.isVisible():
            _stack_native_above(self._video_region_overlay)
        if self._subtitle_overlay.isVisible():
            _stack_native_above(self._subtitle_overlay)
            self._video_region_overlay.set_caption_hit_rect(self._subtitle_overlay.global_hit_rect())
        else:
            self._video_region_overlay.set_caption_hit_rect(None)

    def _on_video_region_double_clicked(self, global_pos: QPoint) -> None:
        self._subtitle_overlay.try_begin_edit_at_global(global_pos)

    def _position_fullscreen_button(self, host: QWidget) -> None:
        if self._mpv_widget.parent() is host:
            self._mpv_widget.lower()
        margin = _VideoOverlayHost._OVERLAY_MARGIN
        self._fullscreen_btn.move(
            max(0, host.width() - self._fullscreen_btn.width() - margin),
            max(0, host.height() - self._fullscreen_btn.height() - margin),
        )
        self._fullscreen_btn.raise_()
        self._fullscreen_btn.show()

    def _set_fullscreen_controls_visible(self, visible: bool) -> None:
        if not self._is_fullscreen:
            return
        window = self._fullscreen_window
        if window is None:
            return
        if visible:
            self._position_fullscreen_button(window)
        else:
            self._fullscreen_btn.hide()

    def enter_fullscreen(self) -> None:
        if self._is_fullscreen or not self.mpv_available:
            return
        if self._fullscreen_window is None:
            self._fullscreen_window = _PreviewFullscreenWindow(self)
        self._video_layout_slot.removeWidget(self._mpv_widget)
        self._mpv_widget.setParent(self._fullscreen_window)
        self._fullscreen_window._layout.addWidget(self._mpv_widget)
        self._mpv_widget.show()
        self._fullscreen_btn.setParent(self._fullscreen_window)
        self._mount_subtitle_overlay(self._fullscreen_window)
        self._is_fullscreen = True
        self._sync_preview_guidance()
        self._fullscreen_window.showFullScreen()
        self._position_fullscreen_button(self._fullscreen_window)
        QTimer.singleShot(0, self._raise_video_overlay)
        self._fullscreen_window.setFocus()
        self._fullscreen_btn.setIcon(self._fullscreen_exit_icon)
        self._fullscreen_btn.setToolTip("Exit fullscreen (Alt+Enter to toggle, Esc to exit)")
        self.fullscreen_changed.emit(True)
        self.sync_subtitle_overlay()
        self.hide_flag_toast()
        self._position_original_mark()

    def exit_fullscreen(self) -> None:
        if not self._is_fullscreen:
            return
        window = self._fullscreen_window
        if window is not None:
            window.restore_cursor()
            window._layout.removeWidget(self._mpv_widget)
            window.hide()
        self._mpv_widget.setParent(self._video_host)
        self._video_layout_slot.addWidget(self._mpv_widget)
        self._video_host.set_overlay(self._fullscreen_btn)
        self._mount_subtitle_overlay(self._video_host)
        self._is_fullscreen = False
        self._sync_preview_guidance()
        self._fullscreen_btn.setIcon(self._fullscreen_enter_icon)
        self._fullscreen_btn.setToolTip("Fullscreen video (Alt+Enter to toggle, Esc to exit)")
        self.fullscreen_changed.emit(False)
        QTimer.singleShot(0, self._raise_video_overlay)
        self.sync_subtitle_overlay()
        self.hide_flag_toast()

    def _mount_video_widget(self) -> None:
        self.exit_fullscreen()
        if self._overlay_stacking_embed is not None:
            try:
                self._overlay_stacking_embed.native_window_ready.disconnect(
                    self._raise_video_overlay,
                )
            except (TypeError, RuntimeError):
                pass
            self._overlay_stacking_embed = None
        while self._video_layout_slot.count():
            item = self._video_layout_slot.takeAt(0)
            if item.widget() is not None:
                old = item.widget()
                # Force deterministic teardown of previous libmpv instance before creating another.
                if hasattr(old, "backend"):
                    try:
                        old.backend.shutdown()  # type: ignore[attr-defined]
                    except Exception:
                        pass
                try:
                    old.close()
                except Exception:
                    pass
                old.deleteLater()

        if is_mpv_available():
            self._mpv_widget = MpvEmbedWidget(self)
            self._backend = self._mpv_widget.backend
            self._backend.position_changed.connect(self._relay_position)
            self._backend.playback_state_changed.connect(self._on_playback_state)
        else:
            self._mpv_widget = MpvUnavailableWidget(self)
            self._backend = None

        self._play_btn.setEnabled(self._backend is not None)
        self._fullscreen_btn.setEnabled(self._backend is not None)
        if self._backend is not None:
            self._backend.set_subtitles_visible(self._subtitles_visible)
        self._sync_audio_devices()
        self._sync_volume()

        self._video_layout_slot.addWidget(self._mpv_widget)
        self._wire_mpv_overlay_stacking()
        self._sync_subtitles_toggle()
        self._sync_preview_guidance()

    def _wire_mpv_overlay_stacking(self) -> None:
        embed = _mpv_embed_widget(self._mpv_widget)
        if embed is None:
            return
        if self._overlay_stacking_embed is embed:
            QTimer.singleShot(0, self._raise_video_overlay)
            return
        previous = self._overlay_stacking_embed
        if previous is not None:
            try:
                previous.native_window_ready.disconnect(self._raise_video_overlay)
            except (TypeError, RuntimeError):
                pass
        self._overlay_stacking_embed = embed
        embed.native_window_ready.connect(self._raise_video_overlay)
        QTimer.singleShot(0, self._raise_video_overlay)

    def cancel_pending_work(self) -> None:
        """Invalidate retry timers and disconnect load handlers (call before project close)."""
        self._sync_generation += 1
        self._media_setup_generation = None
        self._pending_edits_lavfi = False
        self._last_lavfi_graph = None
        self._last_lavfi_aid = None
        self._pending_seek_s = None
        self._lavfi_apply_timer.stop()
        self.cancel_pending_seeks()
        self._disconnect_load_handlers()
        self._media_load_completed = False
        self._load_settle_generation = None
        self._load_settle_attempt = 0
        self._start_drift_suppress_timer.stop()

    def _relay_position(self, seconds: float) -> None:
        # mpv can report a brief non-zero time-pos while track/lavfi setup settles.
        if self._load_settle_generation is not None:
            return
        if self._start_drift_suppress_timer.isActive() and seconds < 1.0:
            return
        if self._skip_ignore_until is not None:
            boundary = self._skip_ignore_until
            if self._skip_ignore_reverse:
                stale = seconds > boundary + 0.05
            else:
                stale = seconds + 0.05 < boundary
            if stale:
                return
            self._clear_skip_ignore()
        reverse = self._playback_rate < 0.0
        if self._is_playing and self._subtitle_overlay_seconds >= 0.0:
            # Drop a stale tick from before a seek. Forward play ignores a jump
            # backward; reverse play ignores a jump forward.
            if reverse and seconds > self._subtitle_overlay_seconds + 0.15:
                return
            if not reverse and seconds < self._subtitle_overlay_seconds - 0.15:
                return
        if (
            self._edits_enabled
            and self._skip_jumps_enabled
            and self._is_playing
            and self._jump_skip_if_needed(seconds)
        ):
            return
        self._subtitle_overlay_seconds = seconds
        self._update_subtitle_overlay_at(seconds)
        shown = self._exact_seek_retry_s if self._exact_seek_retry_s is not None else seconds
        self._keyframe_timeline.set_playhead(shown)
        self._maybe_refresh_lavfi_video_window(shown)
        self.position_changed.emit(seconds)

    def _clear_skip_ignore(self) -> None:
        self._skip_ignore_until = None
        self._skip_ignore_reverse = False

    def _refresh_skip_ranges(self) -> None:
        """Rebuild the sorted skip list. Playback ticks reuse it until an edit changes."""
        profile = self._playback_profile if self._edits_enabled else PlaybackProfile.none()
        self._skip_ranges = skip_ranges_from_project(self._project, profile=profile)

    def _jump_skip_if_needed(self, seconds: float) -> bool:
        """Seek past an enabled skip. Returns True when a jump was issued."""
        ranges = self._skip_ranges
        if self._playback_rate < 0.0:
            entry_s = skip_rewind_seconds(seconds, ranges)
            if entry_s is None:
                return False
            # A skip that starts at 0 cannot be rewound out of; stop instead of looping.
            if skip_exit_seconds(entry_s, ranges) is not None:
                if self._backend is not None:
                    self._backend.pause()
                return True
            self._skip_ignore_until = entry_s
            self._skip_ignore_reverse = True
            self._subtitle_overlay_seconds = entry_s
            self.seek(entry_s, reference="absolute+exact")
            self._keyframe_timeline.set_playhead(entry_s)
            self.position_changed.emit(entry_s)
            return True
        exit_s = skip_exit_seconds(seconds, ranges)
        if exit_s is None:
            return False
        self._skip_ignore_until = exit_s
        self._skip_ignore_reverse = False
        self._subtitle_overlay_seconds = exit_s
        self.seek(exit_s, reference="absolute+exact")
        self._keyframe_timeline.set_playhead(exit_s)
        self.position_changed.emit(exit_s)
        return True

    def _skip_aware_target(self, seconds: float) -> float:
        """While Edited playback is running, land outside any skip containing the target."""
        if not (self._edits_enabled and self._skip_jumps_enabled and self._is_playing):
            return max(0.0, float(seconds))
        ranges = self._skip_ranges
        if self._playback_rate < 0.0:
            entry_s = skip_rewind_seconds(seconds, ranges)
            if entry_s is None or skip_exit_seconds(entry_s, ranges) is not None:
                return max(0.0, float(seconds))
            self._skip_ignore_until = entry_s
            self._skip_ignore_reverse = True
            return entry_s
        exit_s = skip_exit_seconds(seconds, ranges)
        if exit_s is None:
            return max(0.0, float(seconds))
        self._skip_ignore_until = exit_s
        self._skip_ignore_reverse = False
        return exit_s

    def sync_subtitle_overlay(self, *, seconds: float | None = None) -> None:
        # Cue sets arrive after the file opens (Whisper, extract). The checkbox
        # was only refreshed when the project was assigned, so it stayed off.
        self._apply_subtitles_checkbox()
        if seconds is None and self._backend is not None:
            try:
                seconds = float(self._backend.position())
            except Exception:
                seconds = 0.0
        resolved = seconds or 0.0
        self._subtitle_cursor.reset()
        self._subtitle_overlay_seconds = resolved
        self._update_subtitle_overlay_at(resolved)

    def _reset_subtitle_overlay_cache(self) -> None:
        self._subtitle_overlay_seconds = -1.0
        self._subtitle_cursor.reset()
        self._subtitle_display_cue_id = None
        self._subtitle_display_text = ""
        self._subtitle_display_visible = False
        self._subtitle_display_editable = False

    def _on_subtitle_overlay_edit_started(self) -> None:
        if self._backend is not None and self._is_playing:
            self._backend.pause()
        self.subtitle_edit_started.emit()

    def _on_subtitle_overlay_committed(self, cue_id: str, text: str) -> None:
        self.subtitle_cue_edited.emit(cue_id, text)

    @property
    def subtitle_overlay_editing(self) -> bool:
        return self._subtitle_overlay.is_editing

    def _update_subtitle_overlay_at(self, seconds: float) -> None:
        if self._subtitle_overlay.is_editing:
            return
        visible = self._subtitles_visible
        cues = self._project.subtitle_cues if self._project is not None else []
        if self._subtitle_cursor.holds(cues, seconds):
            cue = self._subtitle_cursor.current
        else:
            cue = self._subtitle_cursor.cue_at(cues, seconds)
        cue_id = cue.id if cue is not None else None
        line = cue.edited_text if cue is not None else ""
        editable = cue is not None and not self._is_playing
        if (
            cue_id == self._subtitle_display_cue_id
            and line == self._subtitle_display_text
            and visible == self._subtitle_display_visible
            and editable == self._subtitle_display_editable
        ):
            return
        self._subtitle_display_cue_id = cue_id
        self._subtitle_display_text = line
        self._subtitle_display_visible = visible
        self._subtitle_display_editable = editable
        if cue is None:
            self._subtitle_overlay.show_cue(
                cue_id=None,
                text="",
                visible=visible,
                editable=False,
            )
        else:
            self._subtitle_overlay.show_cue(
                cue_id=cue.id,
                text=line,
                visible=visible,
                editable=editable,
            )
        self._stack_caption_above_region()

    def _disconnect_backend_signals(self, backend: object | None) -> None:
        if backend is None:
            return
        for signal, slot in (
            (getattr(backend, "position_changed", None), self._relay_position),
            (getattr(backend, "playback_state_changed", None), self._on_playback_state),
        ):
            if signal is None:
                continue
            try:
                signal.disconnect(slot)
            except (TypeError, RuntimeError):
                pass

    def _shutdown_embedded_player(self, *, for_modal: bool) -> None:
        """Stop libmpv and disconnect Qt signals before removing the embed widget."""
        self._disconnect_load_handlers()
        embed = _mpv_embed_widget(self._mpv_widget)
        backend = embed.backend if embed is not None else self._backend
        self._disconnect_backend_signals(backend)
        if embed is not None:
            if for_modal:
                embed.suspend_for_modal()
            else:
                embed.backend.shutdown()
        elif self._backend is not None:
            self._backend.shutdown()

    def _remove_video_widget(self) -> None:
        while self._video_layout_slot.count():
            item = self._video_layout_slot.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.setParent(None)
                widget.deleteLater()
        self._backend = None

    def reset_to_idle(self) -> None:
        """Unload media and restore idle preview without tearing down the mpv embed."""
        self.exit_fullscreen()
        self.cancel_pending_work()
        self._reset_subtitle_overlay_cache()
        self._project = None
        self._refresh_skip_ranges()
        self._media_path = None
        embed = _mpv_embed_widget(self._mpv_widget)
        backend = embed.backend if embed is not None else self._backend
        if backend is not None:
            backend.unload_media()
        elif not is_mpv_available() and self._video_layout_slot.count() == 0:
            placeholder = QWidget()
            placeholder.setMinimumHeight(240)
            self._mpv_widget = placeholder
            self._video_layout_slot.addWidget(self._mpv_widget)
            self._play_btn.setEnabled(False)
            self._fullscreen_btn.setEnabled(False)
        self._video_host.show()
        self._sync_subtitles_toggle()
        self._sync_preview_guidance()

    def unmount_mpv_embed(self) -> None:
        """Destroy the native mpv embed widget (Windows file dialogs deadlock on reuse)."""
        self.exit_fullscreen()
        self._shutdown_embedded_player(for_modal=True)
        self._remove_video_widget()
        placeholder = QWidget()
        placeholder.setMinimumHeight(240)
        self._mpv_widget = placeholder
        self._video_layout_slot.addWidget(self._mpv_widget)
        self._video_host.show()
        self._sync_preview_guidance()
        enabled = is_mpv_available()
        self._play_btn.setEnabled(enabled)
        self._fullscreen_btn.setEnabled(enabled)

    def prepare_for_modal_dialog(self) -> None:
        """Hide embedded mpv and release the file before a modal file dialog (Windows deadlock fix)."""
        self.exit_fullscreen()
        if sys.platform == "win32":
            self.unmount_mpv_embed()
            return
        self._disconnect_load_handlers()
        if self._backend is not None:
            self._backend.unload_media()
        self._mpv_widget.setVisible(False)

    def _complete_media_load_if_possible(self, trigger: str) -> None:
        if self._media_load_completed:
            return
        if self._backend is None or self._project is None:
            return
        if not self._backend.has_audio_tracks():
            return
        self._media_load_completed = True
        self._disconnect_load_handlers()
        self._after_media_loaded()

    def _reload_media_with_filters(self) -> None:
        """Load the current media path and re-apply track/lavfi after mpv was torn down."""
        if self._media_path is None or self._backend is None:
            return
        self._disconnect_load_handlers()

        def _on_duration_ready(seconds: float) -> None:
            if seconds <= 0:
                return
            self._complete_media_load_if_possible("duration")

        def _on_tracks_ready() -> None:
            self._complete_media_load_if_possible("tracks")

        self._media_setup_generation = None
        self._media_load_completed = False
        self._pending_edits_lavfi = False
        self._last_lavfi_graph = None
        self._last_lavfi_aid = None
        self._pending_seek_s = None
        self._load_ready_handler = _on_duration_ready
        self._tracks_ready_handler = _on_tracks_ready
        self._backend.duration_changed.connect(_on_duration_ready)
        self._backend.tracks_ready.connect(_on_tracks_ready)
        generation = self._sync_generation
        QTimer.singleShot(0, lambda g=generation: self._dispatch_loadfile(g))

    def _dispatch_loadfile(self, generation: int) -> None:
        if generation != self._sync_generation:
            return
        if self._media_path is None or self._backend is None:
            return
        self._backend.load(self._media_path)

    def restore_after_modal_dialog(self) -> None:
        if sys.platform == "win32":
            if is_mpv_available() and _mpv_embed_widget(self._mpv_widget) is None:
                self._remove_video_widget()
                self._mount_video_widget()
                self._reload_media_with_filters()
            self._video_host.show()
            return
        embed = _mpv_embed_widget(self._mpv_widget)
        self._mpv_widget.setVisible(True)
        if embed is not None and self._media_path is not None and self._backend is not None:
            self._reload_media_with_filters()

    def _disconnect_load_ready_handler(self) -> None:
        if self._load_ready_handler is None or self._backend is None:
            self._load_ready_handler = None
            return
        try:
            self._backend.duration_changed.disconnect(self._load_ready_handler)
        except Exception:
            pass
        self._load_ready_handler = None

    def _disconnect_load_handlers(self) -> None:
        self._disconnect_load_ready_handler()
        if self._tracks_ready_handler is None or self._backend is None:
            self._tracks_ready_handler = None
            return
        try:
            self._backend.tracks_ready.disconnect(self._tracks_ready_handler)
        except Exception:
            pass
        self._tracks_ready_handler = None

    def set_project(self, project: Project | None) -> None:
        if project is None or project.source is None:
            self.reset_to_idle()
            return
        self._reset_subtitle_overlay_cache()
        self._project = project
        self._refresh_skip_ranges()
        path = project.source.path
        if not path.is_file():
            self._media_path = None
            self._disconnect_load_handlers()
            if self._backend is not None:
                self._backend.unload_media()
            self._sync_subtitles_toggle()
            self._sync_preview_guidance()
            return
        if self._backend is None and is_mpv_available():
            self._mount_video_widget()
        self._media_path = path
        if self._backend is not None:
            self._reload_media_with_filters()
        self._sync_subtitles_toggle()
        self._sync_volume()
        self._sync_preview_guidance()

    def _after_media_loaded(self) -> None:
        gen = self._sync_generation
        QTimer.singleShot(0, lambda g=gen: self._finish_media_load(g))

    def _finish_media_load(self, generation: int) -> None:
        if generation != self._sync_generation:
            return
        if self._media_setup_generation == generation:
            return
        if self._backend is None or self._project is None:
            return
        track = self._project.selected_track()
        if track is not None and self._backend is not None:
            ordinal = self._project.audio_ordinal_for_stream(track.stream_index)
            self._backend.set_audio_stream(
                track.stream_index,
                audio_ordinal=ordinal,
            )
        if self._backend is not None:
            self._backend.set_playback_rate(self._playback_rate)
            if self._edits_enabled:
                # Passthrough only on load — full edit graph blocks mpv ~10s on the UI thread.
                self._backend.set_lavfi_complex(MPV_PASSTHROUGH)
                self._last_lavfi_graph = MPV_PASSTHROUGH
                self._last_lavfi_aid = getattr(self._backend, "selected_audio_id", None)
                self._pending_edits_lavfi = True
            else:
                graph = compile_playback_lavfi(self._project, edits_enabled=False)
                self._last_lavfi_graph = graph
                self._last_lavfi_aid = getattr(self._backend, "selected_audio_id", None)
                if graph:
                    self._backend.set_lavfi_complex(graph)
        self._media_setup_generation = generation
        self._sync_audio_devices()
        QTimer.singleShot(0, lambda g=generation: self._apply_deferred_subtitles(g))

    def _apply_deferred_subtitles(self, generation: int) -> None:
        if generation != self._sync_generation or self._backend is None:
            return
        # sid=auto scans all subtitle tracks on large files and freezes the UI ~10s.
        if self._subtitles_visible:
            self._backend.set_subtitles_visible(True, sid="no")
        else:
            self._backend.set_subtitles_visible(False)
        self._begin_load_settle(generation)

    def _begin_load_settle(self, generation: int) -> None:
        """Pin transport to 0 after mpv finishes track/lavfi/subtitle setup."""
        if generation != self._sync_generation or self._backend is None:
            return
        self._load_settle_generation = generation
        self._load_settle_attempt = 0
        QTimer.singleShot(0, self._seek_start_after_load_settle)

    def _seek_start_after_load_settle(self) -> None:
        gen = self._load_settle_generation
        if gen is None or gen != self._sync_generation or self._backend is None:
            self._load_settle_generation = None
            return
        self._backend.seek(0.0, reference="absolute+exact")
        try:
            pos = float(self._backend.position())
        except Exception:
            pos = 0.0
        if pos <= 0.05:
            self._complete_load_settle(suppress_start_drift=False)
            return
        if self._load_settle_attempt >= 40:
            self._complete_load_settle(suppress_start_drift=True)
            return
        self._load_settle_attempt += 1
        QTimer.singleShot(50, self._seek_start_after_load_settle)

    def _complete_load_settle(self, *, suppress_start_drift: bool) -> None:
        self._load_settle_generation = None
        if suppress_start_drift:
            self._start_drift_suppress_timer.start()
        pending = self._pending_seek_s
        self._pending_seek_s = None
        if pending is not None and pending > 0.05 and self._backend is not None:
            reference = self._pending_seek_reference or "absolute+exact"
            self.seek(pending, reference=reference)
            self.position_changed.emit(pending)
            self.schedule_video_region_relayout()
        else:
            self.position_changed.emit(0.0)

    def _apply_pending_edits_lavfi(self) -> None:
        if not self._pending_edits_lavfi or not self._edits_enabled:
            return
        self._pending_edits_lavfi = False
        self.reload_lavfi()

    def refresh_audio_devices(self) -> None:
        self._sync_audio_devices()

    def audio_output_devices(self) -> tuple[list[tuple[str, str]], str | None]:
        """Return ([(name, description)], current_name) for mpv audio outputs."""
        if self._backend is None:
            return ([], None)
        try:
            rows = list(self._backend.audio_devices())
        except Exception:
            rows = []
        try:
            current = self._backend.current_audio_device()
        except Exception:
            current = None
        return (rows, current)

    def set_audio_output_device(self, name: str) -> bool:
        """Select an mpv audio output device by internal name."""
        if self._backend is None:
            return False
        try:
            self._backend.set_audio_device(name)
            # Keep the combo box in sync if this change came from a menu action.
            self._sync_audio_devices()
            return True
        except Exception:
            return False

    def _apply_subtitles_checkbox(self) -> None:
        has_cues = bool(self._project and self._project.subtitle_cues)
        self._subs_toggle.setEnabled(has_cues)
        if not has_cues:
            self._subs_toggle.setToolTip("Load or generate subtitle cues to preview edited lines")
        else:
            self._subs_toggle.setToolTip("Show or hide edited subtitle overlay on the video preview")

    def _sync_subtitles_toggle(self) -> None:
        self.sync_subtitle_overlay()

    def _sync_audio_devices(self) -> None:
        # Device selection UI was moved to the Playback menu.
        # Keep this hook as a no-op for older call sites.
        return

    def _sync_volume(self) -> None:
        enabled = self._backend is not None
        self._volume_slider.setEnabled(enabled)
        self._volume_btn.setEnabled(enabled)
        if not enabled:
            self._refresh_volume_icon()
            return
        assert self._backend is not None
        self._syncing_volume = True
        try:
            self._volume_slider.setValue(int(self._backend.volume()))
            is_muted = getattr(self._backend, "is_muted", None)
            self._muted = bool(is_muted()) if callable(is_muted) else False
        finally:
            self._syncing_volume = False
        if self._volume_slider.value() > 0:
            self._volume_before_mute = int(self._volume_slider.value())
        self._refresh_volume_icon()

    def _refresh_volume_icon(self) -> None:
        muted = self._muted or self._volume_slider.value() == 0
        self._volume_btn.setIcon(self._volume_muted_icon if muted else self._volume_unmuted_icon)
        self._volume_btn.setToolTip("Unmute preview" if muted else "Mute preview")

    def _set_muted(self, muted: bool) -> None:
        if self._backend is None:
            return
        if muted:
            current = int(self._volume_slider.value())
            if current > 0:
                self._volume_before_mute = current
            self._muted = True
            set_muted = getattr(self._backend, "set_muted", None)
            if callable(set_muted):
                set_muted(True)
        else:
            self._muted = False
            set_muted = getattr(self._backend, "set_muted", None)
            if callable(set_muted):
                set_muted(False)
            if self._volume_slider.value() == 0:
                restore = max(1, int(self._volume_before_mute))
                self._syncing_volume = True
                try:
                    self._volume_slider.setValue(restore)
                finally:
                    self._syncing_volume = False
                self._backend.set_volume(restore)
        self._refresh_volume_icon()

    def _on_volume_icon_clicked(self) -> None:
        if self._backend is None:
            return
        if self._muted or self._volume_slider.value() == 0:
            self._set_muted(False)
        else:
            self._set_muted(True)

    def edits_enabled(self) -> bool:
        return self._edits_enabled

    def playback_profile(self) -> PlaybackProfile:
        return self._playback_profile

    def set_playback_profile(self, profile: PlaybackProfile) -> None:
        """Apply a CorrMedia-style filter to Edited preview. Does not write settings."""
        if profile == self._playback_profile:
            return
        self._playback_profile = profile
        self._sync_profile_button()
        self._refresh_skip_ranges()
        if self._edits_enabled:
            self._pending_edits_lavfi = False
            self.reload_lavfi()
        self.playback_filter_changed.emit()

    def open_playback_profile(self) -> None:
        """Show the category and action filter next to the profile button."""
        existing = self._profile_popup
        if existing is not None:
            existing.close()
        popup = PlaybackProfilePopup(self._playback_profile, self.window())
        popup.profile_changed.connect(self._on_playback_profile_picked)
        popup.show_beside(self._profile_btn)
        self._profile_popup = popup

    def _on_playback_profile_picked(self, profile: object) -> None:
        if not isinstance(profile, PlaybackProfile):
            return
        save_playback_profile(profile)
        self.set_playback_profile(profile)

    def _sync_profile_button(self) -> None:
        profile = self._playback_profile
        self._profile_btn.setText(f"{profile.button_label()}  ▾")
        self._profile_btn.setToolTip(profile.status_text())
        font = self._profile_btn.font()
        font.setBold(not profile.is_full())
        self._profile_btn.setFont(font)

    def set_edits_enabled(self, enabled: bool) -> None:
        enabled = bool(enabled)
        changed = self._edits_enabled != enabled
        self._edits_enabled = enabled
        if not enabled:
            self._clear_skip_ignore()
        self._ab_toggle.blockSignals(True)
        self._ab_toggle.setChecked(enabled)
        self._ab_toggle.blockSignals(False)
        self._sync_ab_label()
        self._sync_original_mark()
        if not changed:
            return
        self._pending_edits_lavfi = False
        self._refresh_skip_ranges()
        self.reload_lavfi()
        self.playback_filter_changed.emit()

    def _defer_edit_lavfi_until_media_ready(self) -> bool:
        """True until mpv has finished open + start settle (avoid greying the embed)."""
        if self._backend is None:
            return True
        if self._media_path is None:
            return False
        if not self._media_load_completed:
            return True
        if self._media_setup_generation != self._sync_generation:
            return True
        if self._load_settle_generation is not None:
            return True
        return False

    def _should_queue_seek(self) -> bool:
        """True while opening/settling — user seeks must not fight the start pin."""
        if self._media_path is None or self._backend is None:
            return False
        if not self._media_load_completed:
            return True
        if self._media_setup_generation != self._sync_generation:
            return True
        if self._load_settle_generation is not None:
            return True
        return False

    def _has_zoom_operations(self) -> bool:
        if self._project is None:
            return False
        return any(
            op.enabled and op.type == OperationType.ZOOM
            for op in self._project.timeline_operations()
        )

    def _should_apply_zoom_preview(self) -> bool:
        """Punch-in while playing, or while paused with no authoring box.

        A selected visual modifier is drawn in source-frame coordinates. While
        that box is on screen and playback is stopped, the picture must stay the
        full frame so the overlay lines up. Playback hides the box and shows
        the punch-in.
        """
        if self._is_playing:
            return True
        return not self._video_region_overlay.is_active

    def _sync_zoom_preview_mode(self) -> None:
        wanted = self._should_apply_zoom_preview()
        if wanted == self._apply_zoom_preview:
            return
        self._apply_zoom_preview = wanted
        if not self._has_zoom_operations():
            return
        if wanted:
            self.reload_lavfi()
            return
        self._lavfi_apply_timer.stop()
        self._flush_lavfi_apply()

    def reload_lavfi(self) -> None:
        if self._backend is None or self._project is None:
            return
        delay = self._lavfi_reload_delay_ms
        self._lavfi_reload_delay_ms = 0
        if delay <= 0 and self.is_video_region_dragging:
            delay = LAVFI_DRAG_DEBOUNCE_MS
        self._lavfi_apply_timer.start(max(0, delay))

    def _playhead_for_lavfi_window(self) -> float:
        if self._backend is None:
            return 0.0
        try:
            return max(0.0, float(self._backend.position()))
        except Exception:
            return 0.0

    def _video_window_for_playhead(self, playhead_s: float) -> tuple[float, float]:
        radius = self._lavfi_window_radius_s
        center = max(0.0, float(playhead_s))
        return (center - radius, center + radius)

    def _lavfi_window_needs_refresh(self, playhead_s: float) -> bool:
        window = self._lavfi_video_window
        if window is None:
            return True
        margin = self._lavfi_window_rebuild_margin_s
        return playhead_s < window[0] + margin or playhead_s > window[1] - margin

    def _maybe_refresh_lavfi_video_window(self, playhead_s: float) -> None:
        if not self._edits_enabled or self._project is None or self._backend is None:
            return
        if self._lavfi_apply_timer.isActive():
            return
        if not self._lavfi_window_needs_refresh(playhead_s):
            return
        self.reload_lavfi()

    def _flush_lavfi_apply(self) -> None:
        if self._backend is None or self._project is None:
            return
        if self._defer_edit_lavfi_until_media_ready():
            self._pending_edits_lavfi = self._edits_enabled
            return
        playhead_s = self._playhead_for_lavfi_window()
        video_window = self._video_window_for_playhead(playhead_s)
        graph = compile_playback_lavfi(
            self._project,
            edits_enabled=self._edits_enabled,
            apply_zoom=self._apply_zoom_preview,
            playhead_s=playhead_s if self._edits_enabled else None,
            video_window_radius_s=self._lavfi_window_radius_s,
            profile=self._playback_profile,
        )
        aid = getattr(self._backend, "selected_audio_id", None)
        # The template often stays identical across languages (two stereo dubs).
        # mpv still has the previous [aidN] pad until the graph is applied again.
        if graph == self._last_lavfi_graph and aid == self._last_lavfi_aid:
            self._lavfi_video_window = video_window if self._edits_enabled else None
            return
        self._last_lavfi_graph = graph
        self._last_lavfi_aid = aid
        self._lavfi_video_window = video_window if self._edits_enabled else None
        self._backend.set_lavfi_complex(graph)
        # Paused mpv keeps the old frame until an exact seek after lavfi-complex.
        if not self._backend.is_playing():
            t = max(0.0, float(self._backend.position()))
            token = self._seek_token
            QTimer.singleShot(0, lambda: self._seek_if_current(token, t))

    def sync_track_and_filters(
        self,
        *,
        attempt: int = 0,
        _generation: int | None = None,
        restore_seek: bool = True,
        lavfi_delay_ms: int = 0,
    ) -> None:
        """Re-select mpv audio track and refresh lavfi after track/ops changes."""
        gen = self._sync_generation if _generation is None else _generation
        if gen != self._sync_generation:
            return
        self._refresh_skip_ranges()
        if self._backend is None or self._project is None:
            return
        self._lavfi_reload_delay_ms = max(0, int(lavfi_delay_ms))
        if self._defer_edit_lavfi_until_media_ready():
            # Don't touch aid / lavfi mid-open — that leaves the embed grey forever.
            self._pending_edits_lavfi = self._edits_enabled
            self._lavfi_reload_delay_ms = 0
            return
        # mpv can advance the reported time-pos slightly when switching tracks / rebuilding
        # lavfi-complex (decode boundary / stream reinit). Preserve the user's playhead.
        try:
            desired_time = float(self._backend.position())
        except Exception:
            desired_time = 0.0
        track = self._project.selected_track()
        if track is not None:
            ordinal = self._project.audio_ordinal_for_stream(track.stream_index)
            if not self._backend.set_audio_stream(
                track.stream_index,
                audio_ordinal=ordinal,
            ):
                if attempt < 20:
                    self._lavfi_reload_delay_ms = 0
                    QTimer.singleShot(
                        100,
                        lambda: self.sync_track_and_filters(
                            attempt=attempt + 1,
                            _generation=gen,
                            restore_seek=restore_seek,
                            lavfi_delay_ms=lavfi_delay_ms,
                        ),
                    )
                    return
        self.reload_lavfi()
        if (
            restore_seek
            and desired_time > 0
            and gen == self._sync_generation
            and not self._backend.is_playing()
        ):
            token = self._seek_token
            QTimer.singleShot(0, lambda: self._seek_if_current(token, desired_time))

    def cancel_pending_seeks(self) -> None:
        """Drop delayed Start/End / lavfi restore seeks so they cannot pause Play."""
        self._seek_token += 1
        self._exact_seek_retry_s = None
        self._exact_seek_timer.stop()
        self._keyframe_scrub_timer.stop()
        self._pending_keyframe_scrub_s = None

    def _seek_if_current(self, token: int, seconds: float) -> None:
        if token != self._seek_token or self._backend is None:
            return
        if self._backend.is_playing():
            return
        self.seek(seconds, reference="absolute+exact")

    def exact_seek_target(self) -> float | None:
        return self._exact_seek_retry_s

    def queue_keyframe_scrub_seek(self, seconds: float) -> None:
        """Keyframe seek while the box track is dragged. The latest target wins."""
        self._exact_seek_retry_s = None
        self._exact_seek_timer.stop()
        self._pending_keyframe_scrub_s = max(0.0, float(seconds))
        if not self._keyframe_scrub_timer.isActive():
            self._flush_keyframe_scrub_seek()
            self._keyframe_scrub_timer.start()

    def finish_keyframe_scrub_seek(self, seconds: float) -> None:
        """One exact seek when the box-track drag ends. No follow-up retry."""
        self._keyframe_scrub_timer.stop()
        self._pending_keyframe_scrub_s = None
        self._exact_seek_retry_s = None
        self._exact_seek_timer.stop()
        self.seek(max(0.0, float(seconds)), reference="absolute+exact")

    def _flush_keyframe_scrub_seek(self) -> None:
        seconds = self._pending_keyframe_scrub_s
        self._pending_keyframe_scrub_s = None
        if seconds is None:
            return
        self.seek(seconds, reference="absolute+keyframes")

    def seek_exact(self, seconds: float, *, retry_ms: int = 50) -> None:
        """Exact seek for a paused still frame; cancelled automatically on Play."""
        self.cancel_pending_seeks()
        target = max(0.0, float(seconds))
        if self._should_queue_seek():
            self._pending_seek_s = target
            self._pending_seek_reference = "absolute+exact"
            self._subtitle_overlay_seconds = target
            self._update_subtitle_overlay_at(target)
            return
        self.seek(target, reference="absolute+exact")
        self._exact_seek_retry_s = target
        if retry_ms > 0:
            self._exact_seek_timer.start(retry_ms)

    def _flush_exact_seek_retry(self) -> None:
        seconds = self._exact_seek_retry_s
        self._exact_seek_retry_s = None
        if seconds is None or self._backend is None or self._backend.is_playing():
            return
        self.seek(seconds, reference="absolute+exact")

    def seek(self, seconds: float, *, reference: str = "absolute+keyframes") -> None:
        if self._backend is None:
            return
        target = self._skip_aware_target(seconds)
        if self._should_queue_seek():
            self._pending_seek_s = target
            self._pending_seek_reference = reference
            self._subtitle_overlay_seconds = target
            self._update_subtitle_overlay_at(target)
            return
        self._start_drift_suppress_timer.stop()
        self._subtitle_overlay_seconds = target
        self._backend.seek(target, reference=reference)
        self._update_subtitle_overlay_at(target)

    def seek_to_start(self) -> None:
        if self._backend is None:
            return
        self._backend.seek(0.0)
        self.position_changed.emit(0.0)

    def seek_to_end(self) -> None:
        if self._backend is None:
            return
        end = self._backend.duration()
        if end <= 0:
            return
        self._backend.seek(end)
        self.position_changed.emit(end)

    def playback_rate(self) -> float:
        return self._playback_rate

    def set_playback_rate(self, rate: float) -> None:
        """Set the signed preview rate. Pause keeps this rate for the next play."""
        applied = float(rate)
        if self._backend is not None:
            setter = getattr(self._backend, "set_playback_rate", None)
            if callable(setter):
                applied = float(setter(rate))
        self._playback_rate = applied
        self._sync_speed_ui()

    def stop_shuttle(self) -> None:
        """Resolve K: pause in place and return the shuttle to 1×."""
        self.set_playback_rate(1.0)
        if self._backend is None or not self._backend.is_playing():
            return
        self.cancel_pending_seeks()
        self._backend.pause()

    def shuttle(self, direction: int, *, slow: bool = False) -> None:
        """Step the Resolve-style speed ladder and start playback."""
        playing = self._backend is not None and self._backend.is_playing()
        self.set_playback_rate(
            shuttle_target(self._playback_rate, direction, playing=playing, slow=slow)
        )
        self._begin_playback()

    def attach_speed_menu(self, menu: QMenu) -> None:
        """Add checkable speed presets. Checks refresh when the menu opens."""
        for rate in FORWARD_RATES:
            self._add_speed_action(menu, rate)
        menu.addSeparator()
        for rate in REVERSE_RATES:
            self._add_speed_action(menu, rate)
        menu.aboutToShow.connect(self._sync_speed_ui)

    def _add_speed_action(self, menu: QMenu, rate: float) -> None:
        action = QAction(format_playback_rate(rate), menu)
        action.setCheckable(True)
        action.triggered.connect(lambda _checked=False, picked=rate: self.set_playback_rate(picked))
        menu.addAction(action)
        self._speed_actions.append((action, rate))

    def _sync_speed_ui(self) -> None:
        if not hasattr(self, "_speed_btn"):
            return
        text = format_playback_rate(self._playback_rate)
        self._speed_btn.setText(text)
        off_normal = abs(self._playback_rate - 1.0) > 1e-4
        color = "#e6c36a" if off_normal else "#e8e8e8"
        self._speed_btn.setStyleSheet(
            "QToolButton {"
            f" color: {color};"
            " background: transparent;"
            " border: none;"
            " border-radius: 4px;"
            " padding: 0 6px;"
            " font-size: 12px;"
            "}"
            "QToolButton:hover { background-color: rgba(255, 255, 255, 0.10); }"
            "QToolButton::menu-indicator { image: none; width: 0; }"
        )
        self._sync_speed_button_width()
        for action, preset in self._speed_actions:
            action.setChecked(abs(preset - self._playback_rate) < 1e-4)
        self._reposition_controls()

    def _sync_speed_button_width(self) -> None:
        """Keep the speed button wide enough for the longest rate label."""
        labels = [format_playback_rate(rate) for rate in (*FORWARD_RATES, *REVERSE_RATES)]
        font = self._speed_btn.font()
        font.setPixelSize(12)
        metrics = QFontMetrics(font)
        text_width = max(metrics.horizontalAdvance(label) for label in labels)
        self._speed_btn.setFixedWidth(max(64, text_width + 12))

    def _reposition_controls(self) -> None:
        bar = getattr(self, "_controls_bar", None)
        if isinstance(bar, _CenteredControlBar):
            bar.reposition()

    def toggle_playback(self) -> bool:
        if self._backend is None:
            return False
        if self._backend.is_playing():
            self.cancel_pending_seeks()
            self._backend.pause()
            return False
        self._begin_playback()
        return True

    def _begin_playback(self) -> None:
        if self._backend is None or self._backend.is_playing():
            return
        self.cancel_pending_seeks()
        self._skip_jumps_enabled = True
        self._apply_zoom_preview = True
        self._video_region_overlay.set_preview_playing(True)
        self._stack_caption_above_region()
        self.about_to_play.emit()
        if self._has_zoom_operations():
            self._lavfi_apply_timer.stop()
            self._flush_lavfi_apply()
        if self._edits_enabled:
            try:
                pos = float(self._backend.position())
            except Exception:
                pos = 0.0
            self._jump_skip_if_needed(pos)
        self._apply_pending_edits_lavfi()
        self._backend.play()

    def stop_playback(self) -> None:
        self._stop()

    def toggle_edits(self) -> None:
        self.set_edits_enabled(not self._edits_enabled)

    def frame_step(self, delta: int) -> None:
        if self._backend is None or delta == 0:
            return
        # Drop continuous-playback skip jumps before mpv reports the brief unpause.
        self._skip_jumps_enabled = False
        if self._is_playing:
            self._on_playback_state(False)
        self._backend.frame_step(delta)

    def _toggle_playback(self) -> None:
        self.toggle_playback()

    def _stop(self) -> None:
        if self._backend is not None:
            self._backend.stop()
            self.position_changed.emit(0.0)

    def _on_ab_toggled(self, checked: bool) -> None:
        self.set_edits_enabled(checked)

    def _on_subs_toggled(self, checked: bool) -> None:
        self._subtitles_visible = checked
        if self._backend is not None:
            # Project subtitles render in the Qt overlay — keep mpv subs off (sid=auto freezes UI).
            self._backend.set_subtitles_visible(False)
        self.sync_subtitle_overlay()

    def resizeEvent(self, event: QEvent) -> None:  # noqa: N802
        super().resizeEvent(event)
        if not self._is_fullscreen:
            self._reposition_floating_overlays()
            self._schedule_subtitle_relayout()

    def _on_volume_changed(self, value: int) -> None:
        if self._syncing_volume or self._backend is None:
            return
        level = int(value)
        if level > 0:
            self._volume_before_mute = level
            if self._muted:
                self._muted = False
                set_muted = getattr(self._backend, "set_muted", None)
                if callable(set_muted):
                    set_muted(False)
        else:
            self._muted = True
            set_muted = getattr(self._backend, "set_muted", None)
            if callable(set_muted):
                set_muted(True)
        self._backend.set_volume(level)
        self._refresh_volume_icon()

    def _on_playback_state(self, playing: bool) -> None:
        # frame-step unpauses for a single frame. That is not playback.
        if playing and not self._skip_jumps_enabled:
            return
        self._is_playing = playing
        self._play_btn.setIcon(self._pause_icon if playing else self._play_icon)
        self.playback_state_changed.emit(playing)
        self.sync_subtitle_overlay()
        if playing:
            self._video_region_overlay.set_preview_playing(True)
            self._sync_zoom_preview_mode()
            self._stack_caption_above_region()
            return
        self._skip_jumps_enabled = False
        self._clear_skip_ignore()
        self._sync_zoom_preview_mode()
        self._video_region_overlay.set_preview_playing(False)
        self._stack_caption_above_region()
        self.schedule_video_region_relayout()
