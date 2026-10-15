"""Qt subtitle overlay for preview (Phase 7b)."""

from __future__ import annotations

from PySide6.QtCore import QEvent, QPoint, QRect, QRectF, Qt, Signal
from PySide6.QtGui import QKeyEvent
from PySide6.QtWidgets import QApplication, QLabel, QTextEdit, QVBoxLayout, QWidget

from corrcut.domain.subtitle_cue import SubtitleCue
from corrcut.engine.subtitles.display import cue_at_seconds

__all__ = ["SubtitleCue", "SubtitleOverlay", "cue_at_seconds", "floating_window_flags"]

# Windowed preview metrics (scale = 1.0 at the layout reference height).
_BASE_FONT_PX = 18
_BASE_PADDING_V = 8
_BASE_PADDING_H = 14
_BASE_RADIUS = 6
_BASE_H_MARGIN = 48
_BASE_BOTTOM_MARGIN = 56
_BASE_MIN_LABEL_WIDTH = 120
_BASE_EDITOR_MAX_HEIGHT = 120
_MAX_SCALE = 1.45


def floating_window_flags() -> Qt.WindowType:
    """Tool window so captions can stack above the spatial video overlay."""
    return Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint


class SubtitleOverlay(QWidget):
    """Bottom-centered subtitle line; double-click to edit when paused."""

    edit_committed = Signal(str, str)
    edit_started = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setAttribute(Qt.WA_OpaquePaintEvent, False)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WA_NoSystemBackground, True)
        self.setAttribute(Qt.WA_ShowWithoutActivating, True)
        self.setToolTip("Double-click to edit (when paused)")
        self._cue_id: str | None = None
        self._editable = False
        self._editing = False
        self._click_away_filter_installed = False
        self._active_label_index = 0
        self._layout_reference_height: float | None = None
        self._content_rect: QRectF | None = None
        self._labels: list[QLabel] = []

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        for _ in range(2):
            label = QLabel()
            label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            label.setWordWrap(True)
            label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
            label.hide()
            layout.addWidget(label)
            self._labels.append(label)

        self._editor = QTextEdit()
        self._editor.setMaximumHeight(_BASE_EDITOR_MAX_HEIGHT)
        self._editor.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._editor.hide()
        self._editor.installEventFilter(self)
        layout.addWidget(self._editor)
        self.hide()

    @property
    def is_editing(self) -> bool:
        return self._editing

    def _active_label(self) -> QLabel:
        return self._labels[self._active_label_index]

    def _inactive_label(self) -> QLabel:
        return self._labels[1 - self._active_label_index]

    def _scale_for_host(self, host: QWidget) -> float:
        height = max(1, host.height())
        reference = max(1.0, self._layout_reference_height or float(height))
        if abs(height - reference) < 1.0:
            return 1.0
        raw = height / reference
        if raw < 1.0:
            return max(0.9, raw)
        damped = 1.0 + (raw - 1.0) ** 0.5
        return min(_MAX_SCALE, damped)

    def _overlay_style(self, scale: float) -> str:
        font_px = max(10, round(_BASE_FONT_PX * scale))
        pad_v = max(4, round(_BASE_PADDING_V * scale))
        pad_h = max(6, round(_BASE_PADDING_H * scale))
        radius = max(3, round(_BASE_RADIUS * scale))
        return (
            "background-color: rgba(0, 0, 0, 0.72);"
            "color: #ffffff;"
            f"padding: {pad_v}px {pad_h}px;"
            f"border-radius: {radius}px;"
            f"font-size: {font_px}px;"
        )

    def _apply_host_scaling(self, host: QWidget) -> float:
        scale = self._scale_for_host(host)
        style = self._overlay_style(scale)
        for label in self._labels:
            label.setStyleSheet(style)
        self._editor.setStyleSheet(style)
        self._editor.setMaximumHeight(max(40, round(_BASE_EDITOR_MAX_HEIGHT * scale)))
        return scale

    def _content_bounds(self, host: QWidget) -> QRect:
        """Picture area in host coordinates. The full host is used until a frame is known."""
        host_rect = QRect(0, 0, max(1, host.width()), max(1, host.height()))
        rect = self._content_rect
        if rect is None:
            return host_rect
        bounds = QRect(int(rect.x()), int(rect.y()), int(rect.width()), int(rect.height()))
        bounds = bounds.intersected(host_rect)
        if bounds.width() < 2 or bounds.height() < 2:
            return host_rect
        return bounds

    def _max_label_width(self) -> int:
        host = self.parentWidget()
        if host is None:
            return int(_BASE_MIN_LABEL_WIDTH)
        scale = self._scale_for_host(host)
        margin = int(_BASE_H_MARGIN * scale)
        min_width = int(_BASE_MIN_LABEL_WIDTH * scale)
        return max(min_width, self._content_bounds(host).width() - margin * 2)

    def show_cue(
        self,
        *,
        cue_id: str | None,
        text: str,
        visible: bool,
        editable: bool,
    ) -> None:
        if self._editing:
            return
        clean = text.strip()
        if not visible or not clean:
            self._cue_id = None
            self._editable = False
            for label in self._labels:
                label.clear()
                label.hide()
            self.hide()
            return

        host = self.parentWidget()
        if host is not None and self._layout_reference_height is None:
            self._layout_reference_height = max(1.0, float(host.height()))
        if host is not None:
            self._apply_host_scaling(host)

        max_width = self._max_label_width()
        back_buffer = self._inactive_label()
        back_buffer.setMaximumWidth(max_width)
        back_buffer.setText(clean)
        back_buffer.adjustSize()

        self.setUpdatesEnabled(False)
        try:
            self._cue_id = cue_id
            self._editable = editable and cue_id is not None
            front_buffer = self._active_label()
            front_buffer.hide()
            back_buffer.show()
            self._active_label_index = 1 - self._active_label_index
            self._editor.hide()
            self._position_overlay()
            if not self.isVisible():
                self.show()
            self.raise_()
        finally:
            self.setUpdatesEnabled(True)

    def _position_overlay(self) -> None:
        host = self.parentWidget()
        if host is None:
            return
        scale = self._apply_host_scaling(host)
        label = self._active_label()
        max_width = self._max_label_width()
        label.setMaximumWidth(max_width)
        label.adjustSize()
        self.adjustSize()
        bounds = self._content_bounds(host)
        x = bounds.x() + (bounds.width() - self.width()) // 2
        x = max(bounds.x(), min(x, bounds.x() + max(0, bounds.width() - self.width())))
        bottom_margin = int(_BASE_BOTTOM_MARGIN * scale)
        inset = min(bottom_margin, max(4, bounds.height() // 10))
        y = bounds.y() + bounds.height() - self.height() - inset
        y = max(bounds.y(), y)
        if self.isWindow():
            origin = host.mapToGlobal(QPoint(0, 0))
            self.move(origin.x() + x, origin.y() + y)
        else:
            self.move(x, y)
        self.raise_()

    def reposition(
        self,
        host: QWidget,
        *,
        layout_reference_height: float | None = None,
        content_rect: QRectF | None = None,
    ) -> None:
        if layout_reference_height is not None:
            self._layout_reference_height = max(1.0, layout_reference_height)
        # None means the picture fills the host. Store it even while hidden so the
        # next cue lands on the frame instead of the bars.
        self._content_rect = content_rect
        # setParent on a tool window hides it and keeps the old screen position.
        # Place it anyway so the following show() is already on the picture.
        self._apply_host_scaling(host)
        if self._editing:
            scale = self._scale_for_host(host)
            margin = int(_BASE_H_MARGIN * scale)
            min_width = int(_BASE_MIN_LABEL_WIDTH * scale)
            max_width = max(min_width, self._content_bounds(host).width() - margin * 2)
            self._editor.setMaximumWidth(max_width)
            self._editor.adjustSize()
            self.adjustSize()
        elif self._active_label().isVisible():
            label = self._active_label()
            label.setMaximumWidth(self._max_label_width())
            label.adjustSize()
            self.adjustSize()
        self._position_overlay()

    def mouseDoubleClickEvent(self, event) -> None:  # noqa: N802
        if (
            event.button() == Qt.MouseButton.LeftButton
            and self._editable
            and self._cue_id is not None
            and self._active_label().text().strip()
        ):
            self._begin_edit()
            event.accept()
            return
        super().mouseDoubleClickEvent(event)

    def global_hit_rect(self) -> QRect:
        if not self.isVisible():
            return QRect()
        return QRect(self.mapToGlobal(QPoint(0, 0)), self.size())

    def try_begin_edit_at_global(self, global_pos: QPoint) -> bool:
        """Start an edit if *global_pos* lands on this overlay."""
        if (
            not self.isVisible()
            or not self._editable
            or self._cue_id is None
            or not self._active_label().text().strip()
        ):
            return False
        local = self.mapFromGlobal(global_pos)
        if not self.rect().contains(local):
            return False
        self._begin_edit()
        return True

    def _begin_edit(self) -> None:
        if self._cue_id is None or self._editing:
            return
        self._editing = True
        self._install_click_away_filter()
        self.edit_started.emit()
        text = self._active_label().text()
        self._active_label().hide()
        self._editor.setPlainText(text)
        self._editor.show()
        self._position_overlay()
        self._editor.setFocus()
        cursor = self._editor.textCursor()
        cursor.movePosition(cursor.MoveOperation.End)
        self._editor.setTextCursor(cursor)

    def _commit_edit(self) -> None:
        if not self._editing or self._cue_id is None:
            return
        new_text = self._editor.toPlainText()
        cue_id = self._cue_id
        self._finish_edit(display_text=new_text)
        self.edit_committed.emit(cue_id, new_text)

    def _cancel_edit(self) -> None:
        if not self._editing:
            return
        self._finish_edit()

    def _finish_edit(self, *, display_text: str | None = None) -> None:
        self._editing = False
        self._remove_click_away_filter()
        self._editor.hide()
        label = self._active_label()
        if display_text is not None:
            label.setMaximumWidth(self._max_label_width())
            label.setText(display_text)
            label.adjustSize()
        label.show()
        self._position_overlay()

    def _install_click_away_filter(self) -> None:
        app = QApplication.instance()
        if app is None or self._click_away_filter_installed:
            return
        app.installEventFilter(self)
        self._click_away_filter_installed = True

    def _remove_click_away_filter(self) -> None:
        app = QApplication.instance()
        if app is None or not self._click_away_filter_installed:
            return
        app.removeEventFilter(self)
        self._click_away_filter_installed = False

    def _click_targets_overlay(self, widget: QWidget | None) -> bool:
        if widget is None:
            return False
        if widget is self or widget is self._editor:
            return True
        return self.isAncestorOf(widget)

    def _global_click_inside_overlay(self, event: QEvent) -> bool:
        if not hasattr(event, "globalPosition"):
            return False
        global_pos = event.globalPosition().toPoint()
        local = self.mapFromGlobal(global_pos)
        return self.rect().contains(local)

    def eventFilter(self, watched, event) -> bool:  # noqa: N802
        if watched is self._editor:
            if event.type() == QEvent.Type.FocusOut:
                self._commit_edit()
                return False
            if isinstance(event, QKeyEvent) and event.type() == QEvent.Type.KeyPress:
                if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
                    if event.modifiers() & Qt.KeyboardModifier.ShiftModifier:
                        return False
                    self._commit_edit()
                    return True
                if event.key() == Qt.Key.Key_Escape:
                    self._cancel_edit()
                    return True

        if (
            self._editing
            and event.type() == QEvent.Type.MouseButtonPress
            and isinstance(watched, QWidget)
        ):
            target = watched
            if not self._click_targets_overlay(target) and not self._global_click_inside_overlay(
                event
            ):
                self._commit_edit()

        return super().eventFilter(watched, event)
