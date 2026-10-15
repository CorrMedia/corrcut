"""Start/End cover-box overlay stays aligned with the handle being edited."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QRectF
from PySide6.QtWidgets import QApplication

from corrcut.domain.dialogue_target import DialogueTargetStrategy
from corrcut.domain.operation import Operation, OperationType
from corrcut.domain.project import Project, SourceFileRecord, TrackRecord
from corrcut.domain.time_point import TimePoint
from corrcut.ui.editor_window import EditorWindow
from corrcut.ui.video_region_overlay import (
    VideoRegionOverlay,
    _snap_normalized_box,
    apply_overlay_drag,
    locked_source_aspect_resize,
)

_app = QApplication.instance() or QApplication([])


def _cover_project() -> Project:
    op = Operation.create(
        OperationType.COVER,
        0,
        [],
        TimePoint.from_seconds_float(2.0, 48000),
        TimePoint.from_seconds_float(5.0, 48000),
        params={
            "box": {"x": 0.1, "y": 0.1, "width": 0.2, "height": 0.2},
            "box_end": {"x": 0.6, "y": 0.5, "width": 0.35, "height": 0.4},
        },
    )
    track = TrackRecord(
        stream_index=1,
        codec="aac",
        channel_layout="stereo",
        channels=2,
        sample_rate=48000,
        language="eng",
        dialogue_target_strategy=DialogueTargetStrategy.FULL_MIX,
    )
    return Project(
        source=SourceFileRecord(
            path=Path(__file__).with_name("missing_editor_fixture.mkv"),
            fingerprint={"test": True},
            probe={"duration_seconds": 120.0, "subtitle_streams": []},
        ),
        tracks=[track],
        selected_audio_stream_index=1,
        operations=[op],
    )


def test_out_handle_selection_uses_end_overlay_box(editor_window: EditorWindow) -> None:
    project = _cover_project()
    op = project.operations[0]
    editor_window._set_project(project)
    editor_window._timeline.active_trim_endpoint = lambda: "end"  # type: ignore[method-assign]
    editor_window._on_timeline_region_selected(op.id)
    assert editor_window._preview_panel.video_region_endpoint() == "end"


def test_in_handle_selection_uses_start_overlay_box(editor_window: EditorWindow) -> None:
    project = _cover_project()
    op = project.operations[0]
    editor_window._set_project(project)
    editor_window._timeline.active_trim_endpoint = lambda: "start"  # type: ignore[method-assign]
    editor_window._on_timeline_region_selected(op.id)
    assert editor_window._preview_panel.video_region_endpoint() == "start"


def test_snap_normalized_box_flushes_right_edge() -> None:
    snapped = _snap_normalized_box({"x": 0.7, "y": 0.1, "width": 0.2994, "height": 0.2})
    assert snapped["x"] + snapped["width"] == 1.0


def test_snap_normalized_box_keeps_overflow() -> None:
    snapped = _snap_normalized_box(
        {"x": -0.2, "y": 0.1, "width": 0.4, "height": 0.2},
        overflow=True,
    )
    assert snapped["x"] == -0.2
    assert snapped["width"] == 0.4


def test_cover_move_can_hang_off_left_edge() -> None:
    box = apply_overlay_drag(
        {"x": 0.1, "y": 0.2, "width": 0.3, "height": 0.3},
        "move",
        -0.25,
        0.0,
    )
    assert box["x"] < 0.0
    assert abs(box["width"] - 0.3) < 1e-9


def test_zoom_move_stays_in_bounds() -> None:
    box = apply_overlay_drag(
        {"x": 0.1, "y": 0.2, "width": 0.3, "height": 0.3},
        "move",
        -0.25,
        0.0,
        lock_source_aspect=True,
    )
    assert box["x"] == 0.0
    assert box["x"] + box["width"] <= 1.0


def test_start_end_seek_releases_scrub_lock(editor_window: EditorWindow) -> None:
    project = _cover_project()
    op = project.operations[0]
    editor_window._set_project(project)
    editor_window._timeline.select_operation(op.id)
    editor_window._timeline_scrub_active = True
    editor_window._pending_preview_sync_after_scrub = False
    editor_window._on_video_region_endpoint_changed("end")
    assert not editor_window._timeline_scrub_active
    assert editor_window._timeline.playhead_seconds() > 4.0


def test_play_releases_scrub_lock(editor_window: EditorWindow) -> None:
    editor_window._timeline_scrub_active = True
    editor_window._on_preview_playback_state(True)
    assert not editor_window._timeline_scrub_active
    assert editor_window._preview_playing


def test_play_cancels_pending_exact_seek(editor_window: EditorWindow) -> None:
    panel = editor_window._preview_panel
    panel.seek_exact(3.0)
    assert panel._exact_seek_timer.isActive()
    token = panel._seek_token
    panel.cancel_pending_seeks()
    assert not panel._exact_seek_timer.isActive()
    assert panel._seek_token == token + 1
    assert panel._exact_seek_retry_s is None


def test_flush_before_play_does_not_restore_seek(
    editor_window: EditorWindow,
) -> None:
    editor_window._pending_preview_sync_after_scrub = True
    editor_window._timeline_scrub_active = True
    calls: list[bool] = []

    def _sync(*, restore_seek: bool = True, **_kwargs) -> None:
        calls.append(restore_seek)

    editor_window._preview_panel.sync_track_and_filters = _sync  # type: ignore[method-assign]
    editor_window._flush_filters_before_play()
    assert calls == [False]
    assert not editor_window._timeline_scrub_active
    assert not editor_window._pending_preview_sync_after_scrub


def test_preview_panel_does_not_relayout_during_drag(editor_window: EditorWindow) -> None:
    project = _cover_project()
    op = project.operations[0]
    editor_window._set_project(project)
    editor_window._on_timeline_region_selected(op.id)
    overlay = editor_window._preview_panel._video_region_overlay
    overlay._drag_mode = "move"  # noqa: SLF001
    before = QRectF(overlay._content)  # noqa: SLF001
    overlay._content = QRectF(1, 2, 3, 4)  # noqa: SLF001
    editor_window._preview_panel.reposition_video_region_overlay()
    assert overlay._content == QRectF(1, 2, 3, 4)  # noqa: SLF001
    overlay._drag_mode = None  # noqa: SLF001
    overlay._content = before  # noqa: SLF001


def test_zoom_resize_keeps_source_aspect() -> None:
    press = {"x": 0.2, "y": 0.2, "width": 0.3, "height": 0.3}
    box = locked_source_aspect_resize(press, "resize_br", 0.1, 0.0)
    assert box["width"] == box["height"]
    assert box["width"] == 0.4
    assert box["x"] == 0.2
    assert box["y"] == 0.2


def _static_cover_project() -> Project:
    op = Operation.create(
        OperationType.COVER,
        0,
        [],
        TimePoint.from_seconds_float(2.0, 48000),
        TimePoint.from_seconds_float(5.0, 48000),
        params={"box": {"x": 0.1, "y": 0.1, "width": 0.2, "height": 0.2}},
    )
    project = _cover_project()
    project.operations = [op]
    return project


def test_add_keyframe_at_playhead(editor_window: EditorWindow) -> None:
    project = _cover_project()
    op = project.operations[0]
    editor_window._set_project(project)
    editor_window._timeline.select_operation(op.id)
    editor_window._on_timeline_region_selected(op.id)
    editor_window._timeline.set_playhead(3.5, notify=False)
    editor_window._on_video_region_add_keyframe()
    from corrcut.domain.spatial_keyframes import spatial_keyframes

    kfs = spatial_keyframes(op)
    assert len(kfs) == 3
    assert abs(kfs[1].seconds - 3.5) < 1e-6
    assert editor_window._preview_panel.video_region_keyframe_index() == 1


def test_first_add_on_static_box_starts_animation(editor_window: EditorWindow) -> None:
    from corrcut.domain.spatial_keyframes import has_spatial_animation, spatial_keyframes

    project = _static_cover_project()
    op = project.operations[0]
    editor_window._set_project(project)
    editor_window._timeline.select_operation(op.id)
    editor_window._on_timeline_region_selected(op.id)
    track = editor_window._preview_panel._keyframe_timeline
    assert not track.isHidden()
    assert not track.delete_enabled
    editor_window._timeline.set_playhead(3.5, notify=False)
    editor_window._on_video_region_add_keyframe()
    kfs = spatial_keyframes(op)
    assert len(kfs) == 2
    assert abs(kfs[1].seconds - 3.5) < 1e-6
    assert has_spatial_animation(op)
    assert editor_window._preview_panel.video_region_keyframe_index() == 1
    assert track.delete_enabled
    editor_window._on_video_region_remove_keyframe()
    assert len(spatial_keyframes(op)) == 1
    assert not has_spatial_animation(op)


def test_auto_key_creates_pose_at_playhead(editor_window: EditorWindow) -> None:
    from corrcut.domain.spatial_keyframes import has_spatial_animation, spatial_keyframes

    project = _static_cover_project()
    op = project.operations[0]
    editor_window._set_project(project)
    editor_window._timeline.select_operation(op.id)
    editor_window._on_timeline_region_selected(op.id)
    editor_window._timeline.set_playhead(4.0, notify=False)
    editor_window._on_video_region_box_changed({"x": 0.7, "y": 0.2, "width": 0.2, "height": 0.2})
    kfs = spatial_keyframes(op)
    assert has_spatial_animation(op)
    assert len(kfs) == 2
    assert abs(kfs[1].seconds - 4.0) < 1e-6
    assert kfs[0].box["x"] == 0.1
    assert kfs[1].box["x"] == 0.7
    assert editor_window._preview_panel.video_region_keyframe_index() == 1


def test_auto_key_updates_existing_pose(editor_window: EditorWindow) -> None:
    from corrcut.domain.spatial_keyframes import spatial_keyframes

    project = _static_cover_project()
    op = project.operations[0]
    editor_window._set_project(project)
    editor_window._timeline.select_operation(op.id)
    editor_window._on_timeline_region_selected(op.id)
    editor_window._timeline.set_playhead(2.0, notify=False)
    editor_window._on_video_region_box_changed({"x": 0.4, "y": 0.1, "width": 0.2, "height": 0.2})
    kfs = spatial_keyframes(op)
    assert len(kfs) == 1
    assert kfs[0].box["x"] == 0.4


def test_overlay_follows_playhead_between_keys(editor_window: EditorWindow) -> None:
    project = _cover_project()
    op = project.operations[0]
    editor_window._set_project(project)
    editor_window._timeline.select_operation(op.id)
    editor_window._on_timeline_region_selected(op.id)
    editor_window._follow_spatial_overlay(3.5)
    overlay = editor_window._preview_panel._video_region_overlay
    box = overlay._box
    # Held size is the larger end box; the center is halfway between poses.
    assert abs(box["width"] - 0.35) < 1e-6
    assert abs(box["height"] - 0.4) < 1e-6
    assert abs(box["x"] - 0.3125) < 0.02
    assert abs(box["y"] - 0.25) < 0.02
    assert len(overlay._ghosts) == 2
    assert len(overlay._path) == 2


def test_keyframe_track_click_adds_pose(editor_window: EditorWindow) -> None:
    from corrcut.domain.spatial_keyframes import spatial_keyframes

    project = _static_cover_project()
    op = project.operations[0]
    editor_window._set_project(project)
    editor_window._timeline.select_operation(op.id)
    editor_window._on_timeline_region_selected(op.id)
    editor_window._on_video_region_add_keyframe(3.25)
    kfs = spatial_keyframes(op)
    assert len(kfs) == 2
    assert abs(kfs[1].seconds - 3.25) < 1e-6
    track = editor_window._preview_panel._keyframe_timeline
    assert abs(track.key_times[1] - 3.25) < 1e-6
    editor_window._on_video_region_key_moved(1, 4.5)
    kfs = spatial_keyframes(op)
    assert abs(kfs[1].seconds - 4.5) < 1e-6


def test_delete_selected_key_removes_keyframe_not_region(editor_window: EditorWindow) -> None:
    from corrcut.domain.spatial_keyframes import has_spatial_animation, spatial_keyframes

    project = _cover_project()
    op = project.operations[0]
    editor_window._set_project(project)
    editor_window._timeline.select_operation(op.id)
    editor_window._on_timeline_region_selected(op.id)
    editor_window._on_video_region_keyframe_index_changed(1)
    assert has_spatial_animation(op)
    editor_window._delete_selected_operation()
    assert project.operations[0].id == op.id
    assert not has_spatial_animation(op)
    assert len(spatial_keyframes(op)) == 1


def test_delete_selected_region_removes_edit(editor_window: EditorWindow) -> None:
    from corrcut.domain.spatial_keyframes import has_spatial_animation

    project = _cover_project()
    op = project.operations[0]
    editor_window._set_project(project)
    editor_window._timeline.select_operation(op.id)
    editor_window._on_timeline_region_selected(op.id)
    assert has_spatial_animation(op)
    editor_window._delete_selected_operation()
    assert project.operations == []


def test_delete_last_key_keeps_region(editor_window: EditorWindow) -> None:
    from corrcut.domain.spatial_keyframes import spatial_keyframes

    project = _cover_project()
    op = project.operations[0]
    editor_window._set_project(project)
    editor_window._timeline.select_operation(op.id)
    editor_window._on_timeline_region_selected(op.id)
    editor_window._on_video_region_keyframe_index_changed(1)
    editor_window._delete_selected_operation()
    assert len(spatial_keyframes(op)) == 1
    editor_window._on_video_region_keyframe_index_changed(0)
    editor_window._delete_selected_operation()
    assert project.operations[0].id == op.id
    assert len(spatial_keyframes(op)) == 1


def test_click_region_after_key_deletes_region(editor_window: EditorWindow) -> None:
    project = _cover_project()
    op = project.operations[0]
    editor_window._set_project(project)
    editor_window._timeline.select_operation(op.id)
    editor_window._on_timeline_region_selected(op.id)
    editor_window._on_video_region_keyframe_index_changed(1)
    editor_window._on_timeline_region_selected(op.id)
    editor_window._delete_selected_operation()
    assert project.operations == []


def test_playhead_follow_does_not_select_key_for_delete(
    editor_window: EditorWindow,
) -> None:
    project = _cover_project()
    op = project.operations[0]
    editor_window._set_project(project)
    editor_window._timeline.select_operation(op.id)
    editor_window._on_timeline_region_selected(op.id)
    editor_window._follow_spatial_overlay(2.0)
    editor_window._delete_selected_operation()
    assert project.operations == []


def test_zoom_overlay_hides_during_playback() -> None:
    overlay = VideoRegionOverlay()
    overlay.set_region(
        {"x": 0.25, "y": 0.25, "width": 0.5, "height": 0.5},
        lock_source_aspect=True,
    )
    overlay.show()
    overlay.set_preview_playing(True)
    assert not overlay.isVisible()
    overlay.set_preview_playing(False)
    assert overlay.isVisible()
    overlay.close()


def test_cover_overlay_stays_visible_during_playback() -> None:
    overlay = VideoRegionOverlay()
    overlay.set_region({"x": 0.1, "y": 0.1, "width": 0.2, "height": 0.2})
    overlay.show()
    overlay.set_preview_playing(True)
    assert overlay.isVisible()
    overlay.close()
