"""Command-based undo/redo integration for the editor window.

Edits push small commands (see :mod:`corrcut.domain.undo_stack`) that
mutate only the affected objects in place and trigger a *scoped* UI refresh.
Operation edits never rebuild the (potentially large) AI suggestions panel, and
suggestion edits reuse the existing combined refresh — so undo/redo stays
responsive regardless of project size.
"""

from __future__ import annotations

import copy
from collections.abc import Callable
from dataclasses import dataclass

from PySide6.QtGui import QAction

from corrcut.domain.ai_suggestion import AiSuggestion
from corrcut.domain.operation import Operation, OperationType
from corrcut.domain.time_point import TimePoint
from corrcut.domain.undo_stack import UndoCommand, UndoStack


@dataclass
class _OpFields:
    """Snapshot of the mutable fields of a single operation."""

    type: OperationType
    params: dict[str, object]
    channels: list[str]
    start: TimePoint
    end: TimePoint
    enabled: bool

    @classmethod
    def capture(cls, op: Operation) -> _OpFields:
        return cls(
            type=op.type,
            params=copy.deepcopy(op.params),
            channels=list(op.scope.channels),
            start=op.time_range.start,
            end=op.time_range.end,
            enabled=op.enabled,
        )

    def apply(self, op: Operation) -> None:
        op.type = self.type
        op.params = copy.deepcopy(self.params)
        op.scope.channels = list(self.channels)
        op.time_range.start = self.start
        op.time_range.end = self.end
        op.enabled = self.enabled


class EditorUndoMixin:
    def _init_undo_stack(self) -> None:
        self._undo_stack = UndoStack()
        self._undo_in_progress = False
        self._op_gesture: dict[str, _OpFields] = {}
        self._undo_action: QAction | None = None
        self._redo_action: QAction | None = None

    def _clear_undo_stack(self) -> None:
        self._undo_stack.clear()
        self._op_gesture.clear()
        self._update_undo_actions()

    def _push_undo(
        self,
        label: str,
        undo: Callable[[], None],
        redo: Callable[[], None],
    ) -> None:
        if self._undo_in_progress:
            return
        self._undo_stack.push(UndoCommand(label=label, undo=undo, redo=redo))
        self._update_undo_actions()

    # -- gesture helpers (multi-event drags of a single region) ----------------

    def _begin_op_gesture(self, op_id: str) -> None:
        if self._undo_in_progress:
            return
        op = self._find_operation(op_id)
        if op is None or op_id in self._op_gesture:
            return
        self._op_gesture[op_id] = _OpFields.capture(op)

    def _end_op_gesture(self, op_id: str, label: str) -> None:
        before = self._op_gesture.pop(op_id, None)
        op = self._find_operation(op_id)
        if before is None or op is None:
            return
        after = _OpFields.capture(op)
        if before == after:
            return
        self._push_undo(
            label,
            undo=lambda: self._restore_op_fields(op_id, before),
            redo=lambda: self._restore_op_fields(op_id, after),
        )

    def _commit_op_change(
        self,
        op_id: str,
        before: _OpFields,
        label: str,
    ) -> None:
        """Push a command for a discrete (non-gesture) field change."""
        op = self._find_operation(op_id)
        if op is None:
            return
        after = _OpFields.capture(op)
        if before == after:
            return
        self._push_undo(
            label,
            undo=lambda: self._restore_op_fields(op_id, before),
            redo=lambda: self._restore_op_fields(op_id, after),
        )

    # -- command appliers ------------------------------------------------------

    def _restore_op_fields(self, op_id: str, fields: _OpFields) -> None:
        op = self._find_operation(op_id)
        if op is None:
            return
        fields.apply(op)
        self._refresh_operations_panel()
        self._reselect_operation(op_id)

    def _undo_add_operation(self, op: Operation, index: int) -> None:
        ops = self._project.operations if self._project else None
        if ops is None:
            return
        if not any(o.id == op.id for o in ops):
            ops.insert(min(index, len(ops)), op)
        self._refresh_operations_panel()
        self._reselect_operation(op.id)

    def _undo_remove_operation(self, op_id: str) -> None:
        if self._project is None:
            return
        self._project.operations = [o for o in self._project.operations if o.id != op_id]
        self._refresh_operations_panel()

    def _reselect_operation(self, op_id: str) -> None:
        op = self._find_operation(op_id)
        if op is None:
            return
        self._operations_panel.select_operation(op_id, emit_selection_changed=False)
        self._timeline.select_operation(op_id)
        self._operations_panel.update_operation_editor(op)
        self._sync_beep_tone_for_selection()
        self._sync_video_region_overlay(op)
        self._sync_type_tool_strip(op.type)

    # -- suggestion-affecting edits (touch ops + suggestions) ------------------

    def _refresh_after_suggestion_change(self) -> None:
        self._refresh_operations_panel()
        self._refresh_suggestions_panel()
        self._preview_panel.reload_lavfi()

    def _restore_ai_suggestions(self, suggestions: list[AiSuggestion]) -> None:
        if self._project is None:
            return
        self._project.ai_suggestions = list(suggestions)
        self._refresh_suggestions_panel()

    def _run_suggestion_command(
        self,
        label: str,
        mutate: Callable[[], None],
    ) -> None:
        if self._project is None:
            mutate()
            return
        before_ops = list(self._project.operations)
        before_suggestions = [(s, s.status) for s in self._project.ai_suggestions]
        before_op_ids = [op.id for op in before_ops]
        before_status = {s.id: s.status for s, _ in before_suggestions}
        mutate()
        after_ops = list(self._project.operations)
        after_suggestions = [(s, s.status) for s in self._project.ai_suggestions]
        self._refresh_after_suggestion_change()
        self._notify_project_modified()
        if [op.id for op in after_ops] == before_op_ids and all(
            s.status == before_status.get(s.id) for s, _ in after_suggestions
        ):
            return

        before_suggestion_list = [s for s, _ in before_suggestions]
        before_status = {id(s): status for s, status in before_suggestions}
        after_suggestion_list = [s for s, _ in after_suggestions]
        after_status = {id(s): status for s, status in after_suggestions}

        def _restore(
            ops: list[Operation],
            suggestions: list[AiSuggestion],
            statuses: dict[int, object],
        ) -> None:
            if self._project is None:
                return
            self._project.operations = list(ops)
            self._project.ai_suggestions = list(suggestions)
            for suggestion in suggestions:
                status = statuses.get(id(suggestion))
                if status is not None:
                    suggestion.status = status  # type: ignore[assignment]
            self._refresh_after_suggestion_change()

        self._push_undo(
            label,
            undo=lambda: _restore(before_ops, before_suggestion_list, before_status),
            redo=lambda: _restore(after_ops, after_suggestion_list, after_status),
        )

    # -- public undo/redo ------------------------------------------------------

    def _undo(self) -> None:
        if not self._undo_stack.can_undo():
            return
        self._op_gesture.clear()
        self._undo_in_progress = True
        try:
            command = self._undo_stack.undo()
        finally:
            self._undo_in_progress = False
        self._update_undo_actions()
        self._sync_dirty_after_history_step()
        if command is not None:
            self._status.showMessage(f"Undo: {command.label}", 2000)

    def _redo(self) -> None:
        if not self._undo_stack.can_redo():
            return
        self._op_gesture.clear()
        self._undo_in_progress = True
        try:
            command = self._undo_stack.redo()
        finally:
            self._undo_in_progress = False
        self._update_undo_actions()
        self._sync_dirty_after_history_step()
        if command is not None:
            self._status.showMessage(f"Redo: {command.label}", 2000)

    def _update_undo_actions(self) -> None:
        if self._undo_action is not None:
            self._undo_action.setEnabled(self._undo_stack.can_undo())
            label = self._undo_stack.undo_label()
            self._undo_action.setText(f"&Undo {label}" if label else "&Undo")
        if self._redo_action is not None:
            self._redo_action.setEnabled(self._undo_stack.can_redo())
            label = self._undo_stack.redo_label()
            self._redo_action.setText(f"&Redo {label}" if label else "&Redo")
