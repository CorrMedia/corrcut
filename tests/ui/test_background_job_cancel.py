"""Status-bar cancel for silent background prep jobs."""

from __future__ import annotations

from PySide6.QtWidgets import QToolBar

from corrcut.ui.editor_window import EditorWindow


def test_background_cancel_is_hidden_until_a_prep_job_runs(
    editor_window: EditorWindow, qtbot
) -> None:  # type: ignore[no-untyped-def]
    assert editor_window.findChild(QToolBar) is None
    button = editor_window._background_cancel_btn
    action = editor_window._cancel_job_action
    assert button.isHidden()
    assert not action.isEnabled()

    def _work() -> None:
        editor_window._scene_cut_job_runner.cancel_event.wait(2)

    assert editor_window._scene_cut_job_runner.run_callable("Detect scene cuts", _work)
    assert not button.isHidden()
    assert action.isEnabled()

    button.click()
    assert editor_window._scene_cut_job_runner.cancel_event.is_set()
    qtbot.waitUntil(lambda: not editor_window._scene_cut_job_runner.is_busy)
    assert button.isHidden()
    assert not action.isEnabled()
