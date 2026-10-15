"""Drop, delete, and configure timeline review flags."""

from __future__ import annotations

import time

from PySide6.QtCore import Qt
from PySide6.QtGui import QActionGroup, QColor, QIcon, QKeySequence, QPainter, QPixmap, QShortcut
from PySide6.QtWidgets import (
    QAbstractSpinBox,
    QApplication,
    QColorDialog,
    QLineEdit,
    QMenu,
    QPlainTextEdit,
    QTextEdit,
)

from corrcut.domain.timeline_flag import (
    FLAG_SLOTS,
    TimelineFlag,
    normalize_flag_color,
    normalize_flag_slot,
)
from corrcut.ui.flag_colors_dialog import FlagColorsDialog
from corrcut.ui.flag_slots import (
    NUMPAD_SEQUENCES,
    load_flag_slot_styles,
    resolve_flag_color,
)
from corrcut.ui.timeline.timecode import format_timecode

_DROP_DEBOUNCE_S = 0.08
# Only correct onto the decoded frame when it is actually nearby.
# A stale mpv time-pos (common while playing or just after a drag seek)
# must not teleport the flag.
_FLAG_FRAME_SNAP_S = 1.0 / 12.0  # ~2 frames at 24 fps


def _color_swatch_icon(color: QColor) -> QIcon:
    size = 16
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QColor(color))
    painter.drawRoundedRect(1.0, 1.0, size - 2.0, size - 2.0, 3.0, 3.0)
    painter.end()
    return QIcon(pixmap)


def snap_flag_seconds(intended: float, media: float | None) -> float:
    """Keep *intended* unless media is a nearby frame of the same moment."""
    intended = max(0.0, float(intended))
    if media is None:
        return intended
    media = max(0.0, float(media))
    if abs(media - intended) <= _FLAG_FRAME_SNAP_S:
        return media
    return intended


class EditorFlagsMixin:
    def _init_flag_shortcuts(self) -> None:
        self._last_flag_drop_at = 0.0
        self._flag_gesture: dict[str, float] = {}
        for slot, sequence in NUMPAD_SEQUENCES.items():
            shortcut = QShortcut(QKeySequence(sequence), self)
            shortcut.setContext(Qt.ShortcutContext.ApplicationShortcut)
            shortcut.activated.connect(lambda s=slot: self._drop_flag(s))

    def _refresh_flags(self) -> None:
        styles = load_flag_slot_styles()
        flags = self._project.timeline_flags if self._project is not None else []
        self._timeline.sync_flags(flags, styles)

    def _flag_drop_seconds(self) -> float:
        return self._flag_media_seconds(self._timeline.playhead_seconds())

    def _playback_seconds_or_none(self) -> float | None:
        if not getattr(self._preview_panel, "mpv_available", False):
            return None
        getter = getattr(self._preview_panel, "playback_seconds", None)
        if not callable(getter):
            return None
        try:
            return max(0.0, float(getter()))
        except Exception:
            return None

    def _flag_media_seconds(self, intended: float) -> float:
        return snap_flag_seconds(intended, self._playback_seconds_or_none())

    def _align_playhead_to_flag(self, seconds: float) -> None:
        self._timeline.set_playhead(seconds, notify=False)

    def _focus_blocks_flag_shortcut(self) -> bool:
        widget = QApplication.focusWidget()
        return isinstance(widget, (QLineEdit, QTextEdit, QPlainTextEdit, QAbstractSpinBox))

    def _drop_flag(self, slot: int) -> None:
        if self._project is None:
            return
        if self._focus_blocks_flag_shortcut():
            return
        now = time.monotonic()
        if now - self._last_flag_drop_at < _DROP_DEBOUNCE_S:
            return
        self._last_flag_drop_at = now
        seconds = self._flag_drop_seconds()
        flag = TimelineFlag.create(seconds, slot)
        index = len(self._project.timeline_flags)
        self._project.timeline_flags.append(flag)
        self._refresh_flags()
        self._timeline.select_flag(flag.id)
        self._align_playhead_to_flag(seconds)
        self._notify_project_modified()
        self._push_undo(
            f"drop {self._flag_slot_label(slot)} flag",
            undo=lambda: self._undo_remove_flag(flag.id),
            redo=lambda: self._undo_add_flag(flag, index),
        )
        styles = load_flag_slot_styles()
        style = styles.get(slot)
        label = style.display_label() if style is not None else f"Flag {slot}"
        color = style.color if style is not None else None
        timecode = format_timecode(seconds)
        if color is not None:
            shower = getattr(self._preview_panel, "show_flag_toast", None)
            if callable(shower):
                shower(color, label, timecode)
        self._status.showMessage(f"{label} at {timecode}", 2500)

    def _flag_slot_label(self, slot: int) -> str:
        styles = load_flag_slot_styles()
        style = styles.get(slot)
        return style.display_label() if style is not None else f"Flag {slot}"

    def _delete_flag(self, flag_id: str) -> None:
        if self._project is None:
            return
        removed = next((f for f in self._project.timeline_flags if f.id == flag_id), None)
        if removed is None:
            return
        index = self._project.timeline_flags.index(removed)
        self._project.timeline_flags = [
            item for item in self._project.timeline_flags if item.id != flag_id
        ]
        self._refresh_flags()
        self._notify_project_modified()
        self._push_undo(
            "delete flag",
            undo=lambda: self._undo_add_flag(removed, index),
            redo=lambda: self._undo_remove_flag(removed.id),
        )

    def _undo_add_flag(self, flag: TimelineFlag, index: int) -> None:
        flags = self._project.timeline_flags if self._project else None
        if flags is None:
            return
        if not any(item.id == flag.id for item in flags):
            flags.insert(min(index, len(flags)), flag)
        self._refresh_flags()
        self._timeline.select_flag(flag.id)

    def _on_flag_moved(self, flag_id: str, seconds: float, final: bool) -> None:
        if self._project is None:
            return
        flag = next((item for item in self._project.timeline_flags if item.id == flag_id), None)
        if flag is None:
            return
        if flag_id not in self._flag_gesture:
            self._flag_gesture[flag_id] = float(flag.seconds)
        if not final:
            flag.seconds = max(0.0, float(seconds))
            return
        snapped = self._flag_media_seconds(seconds)
        flag.seconds = snapped
        self._refresh_flags()
        self._timeline.select_flag(flag_id)
        self._align_playhead_to_flag(snapped)
        before = self._flag_gesture.pop(flag_id, None)
        after = float(flag.seconds)
        if before is None or abs(before - after) < 1e-6:
            return
        self._notify_project_modified()
        self._push_undo(
            "move flag",
            undo=lambda: self._undo_move_flag(flag_id, before),
            redo=lambda: self._undo_move_flag(flag_id, after),
        )

    def _undo_move_flag(self, flag_id: str, seconds: float) -> None:
        if self._project is None:
            return
        for flag in self._project.timeline_flags:
            if flag.id == flag_id:
                flag.seconds = max(0.0, float(seconds))
                break
        self._refresh_flags()
        self._timeline.select_flag(flag_id)

    def _undo_remove_flag(self, flag_id: str) -> None:
        if self._project is None:
            return
        self._project.timeline_flags = [
            flag for flag in self._project.timeline_flags if flag.id != flag_id
        ]
        self._refresh_flags()

    def _find_flag(self, flag_id: str) -> TimelineFlag | None:
        if self._project is None:
            return None
        return next((item for item in self._project.timeline_flags if item.id == flag_id), None)

    def _flag_display_color(self, flag: TimelineFlag) -> QColor:
        styles = load_flag_slot_styles()
        style = styles.get(flag.slot)
        slot_color = style.color if style is not None else QColor(180, 180, 180)
        return resolve_flag_color(flag.color, slot_color)

    def _apply_flag_style(self, flag_id: str, slot: int, color: str | None) -> None:
        flag = self._find_flag(flag_id)
        if flag is None:
            return
        flag.slot = normalize_flag_slot(slot)
        flag.color = normalize_flag_color(color)
        self._refresh_flags()
        self._timeline.select_flag(flag_id)

    def _set_flag_slot(self, flag_id: str, slot: int) -> None:
        flag = self._find_flag(flag_id)
        if flag is None:
            return
        slot = normalize_flag_slot(slot)
        if flag.slot == slot and flag.color is None:
            return
        before_slot = flag.slot
        before_color = flag.color
        self._apply_flag_style(flag_id, slot, None)
        self._notify_project_modified()
        self._push_undo(
            "change flag color",
            undo=lambda: self._apply_flag_style(flag_id, before_slot, before_color),
            redo=lambda: self._apply_flag_style(flag_id, slot, None),
        )

    def _set_flag_color(self, flag_id: str, color: str | None) -> None:
        flag = self._find_flag(flag_id)
        if flag is None:
            return
        if color is None:
            normalized: str | None = None
        else:
            parsed = QColor(color)
            if not parsed.isValid():
                return
            parsed.setAlpha(255)
            normalized = parsed.name()
        if flag.color == normalized:
            return
        before = flag.color
        slot = flag.slot
        self._apply_flag_style(flag_id, slot, normalized)
        self._notify_project_modified()
        self._push_undo(
            "change flag color",
            undo=lambda: self._apply_flag_style(flag_id, slot, before),
            redo=lambda: self._apply_flag_style(flag_id, slot, normalized),
        )

    def _pick_flag_color(self, flag_id: str) -> None:
        flag = self._find_flag(flag_id)
        if flag is None:
            return
        chosen = QColorDialog.getColor(self._flag_display_color(flag), self, "Flag color")
        if not chosen.isValid():
            return
        chosen.setAlpha(255)
        self._set_flag_color(flag_id, chosen.name())

    def _open_flag_colors_dialog(self) -> None:
        dialog = FlagColorsDialog(self)
        if dialog.exec():
            self._refresh_flags()

    def _flag_context_menu(self, flag_id: str) -> QMenu:
        menu = QMenu(self)
        flag = self._find_flag(flag_id)
        styles = load_flag_slot_styles()
        group = QActionGroup(menu)
        group.setExclusive(True)
        for slot in FLAG_SLOTS:
            style = styles.get(slot)
            if style is None:
                continue
            action = menu.addAction(_color_swatch_icon(style.color), style.display_label())
            action.setCheckable(True)
            action.setChecked(flag is not None and flag.color is None and flag.slot == slot)
            group.addAction(action)
            action.triggered.connect(
                lambda _checked=False, chosen=slot: self._set_flag_slot(flag_id, chosen)
            )
        menu.addAction("Custom color…").triggered.connect(
            lambda _checked=False: self._pick_flag_color(flag_id)
        )
        if flag is not None and flag.color:
            menu.addAction("Use slot color").triggered.connect(
                lambda _checked=False: self._set_flag_color(flag_id, None)
            )
        menu.addSeparator()
        menu.addAction("Delete flag").triggered.connect(
            lambda _checked=False: self._delete_flag(flag_id)
        )
        return menu

    def _on_flag_context_menu(self, flag_id: str, global_pos) -> None:  # type: ignore[no-untyped-def]
        self._flag_context_menu(flag_id).exec(global_pos)
