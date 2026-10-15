"""Command-based undo stack unit tests."""

from __future__ import annotations

from corrcut.domain.undo_stack import UndoCommand, UndoStack


def _counter_command(log: list[str], name: str) -> UndoCommand:
    return UndoCommand(
        label=name,
        undo=lambda: log.append(f"undo:{name}"),
        redo=lambda: log.append(f"redo:{name}"),
    )


def test_push_enables_undo_disables_redo() -> None:
    stack = UndoStack()
    assert not stack.can_undo()
    assert not stack.can_redo()
    stack.push(_counter_command([], "a"))
    assert stack.can_undo()
    assert not stack.can_redo()


def test_undo_then_redo_runs_callables_in_order() -> None:
    log: list[str] = []
    stack = UndoStack()
    stack.push(_counter_command(log, "a"))

    stack.undo()
    assert log == ["undo:a"]
    assert stack.can_redo()

    stack.redo()
    assert log == ["undo:a", "redo:a"]
    assert stack.can_undo()


def test_push_clears_redo() -> None:
    stack = UndoStack()
    stack.push(_counter_command([], "a"))
    stack.undo()
    assert stack.can_redo()
    stack.push(_counter_command([], "b"))
    assert not stack.can_redo()


def test_labels_track_top_of_stack() -> None:
    stack = UndoStack()
    stack.push(_counter_command([], "create"))
    assert stack.undo_label() == "create"
    assert stack.redo_label() is None
    stack.undo()
    assert stack.undo_label() is None
    assert stack.redo_label() == "create"


def test_max_depth_trims_oldest() -> None:
    stack = UndoStack(max_depth=2)
    for name in ("a", "b", "c"):
        stack.push(_counter_command([], name))
    # Oldest ("a") trimmed; only two undos remain.
    assert stack.undo() is not None
    assert stack.undo() is not None
    assert stack.undo() is None
