"""Scene-cuts toolbar toggle."""

from corrcut.ui.editor_window import EditorWindow


def test_scene_cuts_toolbar_toggles_timeline(editor_window: EditorWindow) -> None:
    btn = editor_window._timeline_panel._scene_cuts_btn
    assert btn.isChecked()
    assert editor_window._timeline.snap_to_scene_cuts_enabled()

    btn.click()
    assert not btn.isChecked()
    assert not editor_window._timeline.snap_to_scene_cuts_enabled()

    btn.click()
    assert btn.isChecked()
    assert editor_window._timeline.snap_to_scene_cuts_enabled()
