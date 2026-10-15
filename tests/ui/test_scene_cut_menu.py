"""Scene-cuts toolbar toggle."""

from pathlib import Path

from corrcut.domain.dialogue_target import DialogueTargetStrategy
from corrcut.domain.project import Project, SourceFileRecord, TrackRecord
from corrcut.ui.editor_window import EditorWindow


def test_scene_cuts_toolbar_toggles_timeline(editor_window: EditorWindow) -> None:
    # Timeline tools (including scene-cut snap) stay disabled until media is loaded.
    editor_window._set_project(
        Project(
            source=SourceFileRecord(
                path=Path("missing_scene_cut_fixture.mkv"),
                fingerprint={"test": True},
                probe={"duration_seconds": 60.0, "subtitle_streams": []},
            ),
            tracks=[
                TrackRecord(
                    stream_index=1,
                    codec="aac",
                    channel_layout="stereo",
                    channels=2,
                    sample_rate=48000,
                    language="eng",
                    dialogue_target_strategy=DialogueTargetStrategy.FULL_MIX,
                ),
            ],
            selected_audio_stream_index=1,
        )
    )
    btn = editor_window._timeline_panel._scene_cuts_btn
    assert btn.isEnabled()
    assert btn.isChecked()
    assert editor_window._timeline.snap_to_scene_cuts_enabled()

    btn.click()
    assert not btn.isChecked()
    assert not editor_window._timeline.snap_to_scene_cuts_enabled()

    btn.click()
    assert btn.isChecked()
    assert editor_window._timeline.snap_to_scene_cuts_enabled()
