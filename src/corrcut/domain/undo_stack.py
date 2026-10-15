"""Lightweight command-based undo/redo.

Each edit pushes an :class:`UndoCommand` carrying only the *delta* needed to
reverse or replay it (callables closed over the affected objects), rather than a
full project snapshot. This keeps undo/redo O(1) with respect to project size
and avoids re-serialising every operation and AI suggestion on each step.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass


@dataclass
class UndoCommand:
    label: str
    undo: Callable[[], None]
    redo: Callable[[], None]


class UndoStack:
    def __init__(self, *, max_depth: int = 200) -> None:
        self._undo: list[UndoCommand] = []
        self._redo: list[UndoCommand] = []
        self._max_depth = max_depth

    def clear(self) -> None:
        self._undo.clear()
        self._redo.clear()

    def can_undo(self) -> bool:
        return bool(self._undo)

    def can_redo(self) -> bool:
        return bool(self._redo)

    def undo_label(self) -> str | None:
        return self._undo[-1].label if self._undo else None

    def redo_label(self) -> str | None:
        return self._redo[-1].label if self._redo else None

    def push(self, command: UndoCommand) -> None:
        """Record an already-applied edit. Clears the redo stack."""
        self._undo.append(command)
        if len(self._undo) > self._max_depth:
            self._undo.pop(0)
        self._redo.clear()

    def undo(self) -> UndoCommand | None:
        if not self._undo:
            return None
        command = self._undo.pop()
        command.undo()
        self._redo.append(command)
        return command

    def redo(self) -> UndoCommand | None:
        if not self._redo:
            return None
        command = self._redo.pop()
        command.redo()
        self._undo.append(command)
        return command
