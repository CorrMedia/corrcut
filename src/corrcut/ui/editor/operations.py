"""Timeline operations and AI suggestion review."""

from __future__ import annotations

from PySide6.QtCore import QTimer

from corrcut.ai.pipeline import apply_suggestion, reset_suggestion_to_pending, revoke_suggestion
from corrcut.domain.ai_suggestion import AiSuggestion, SuggestionStatus
from corrcut.domain.operation import (
    DEFAULT_BOX,
    ITEM_LEVEL_STREAM_INDEX,
    CensorParams,
    Operation,
    OperationScope,
    OperationType,
    default_params,
    is_audio_operation,
    is_item_level_operation,
    is_spatial_operation,
    normalize_operation_type,
)
from corrcut.domain.spatial_keyframes import (
    SpatialKeyframe,
    add_spatial_keyframe,
    copy_box,
    has_spatial_animation,
    keyframe_seconds,
    move_spatial_keyframe,
    neighbor_pose_boxes,
    preview_box_at_seconds,
    remove_spatial_keyframe,
    retarget_spatial_keyframes,
    sample_box_at_seconds,
    set_spatial_keyframes,
    spatial_keyframes,
)
from corrcut.domain.time_point import TimePoint
from corrcut.ui.editor.undo import _OpFields
from corrcut.ui.preview_panel import LAVFI_DRAG_DEBOUNCE_MS
from corrcut.ui.timeline.timeline_mode import TimelineMode

# mpv exact-seek often lands a frame off; treat this as "on" a key.
_PLAYHEAD_KEY_SNAP = 0.05


class EditorOperationsMixin:
    def _find_operation(self, op_id: str | None) -> Operation | None:
        if self._project is None or op_id is None:
            return None
        for op in self._project.operations:
            if op.id == op_id:
                return op
        return None

    def _select_spatial_keyframe(self, index: int) -> None:
        self._spatial_selection = "keyframe"
        setter = getattr(self._preview_panel, "set_keyframe_selected", None)
        if callable(setter):
            setter(index)

    def _select_spatial_region(self) -> None:
        self._spatial_selection = "region"
        setter = getattr(self._preview_panel, "set_keyframe_selected", None)
        if callable(setter):
            setter(-1)

    def _sync_beep_tone_for_selection(self) -> None:
        op_id = self._timeline.selected_operation_id()
        self._timeline_panel.set_beep_tone_for_operation(self._find_operation(op_id))

    def _focus_blocks_edit_shortcut(self) -> bool:
        from PySide6.QtWidgets import (
            QAbstractSpinBox,
            QApplication,
            QLineEdit,
            QPlainTextEdit,
            QTextEdit,
        )

        widget = QApplication.focusWidget()
        return isinstance(widget, (QLineEdit, QTextEdit, QPlainTextEdit, QAbstractSpinBox))

    def _sync_type_tool_strip(self, op_type: OperationType | None) -> None:
        mode = (
            TimelineMode.SELECT
            if op_type is None
            else TimelineMode.for_operation_type(op_type)
        )
        self._timeline_panel.set_tool_mode(mode)

    def _apply_select_tool(self) -> None:
        if not self._timeline_tools_allowed() or self._focus_blocks_edit_shortcut():
            return
        if self._timeline.clear_operation_selection():
            self._on_timeline_region_deselected()
        else:
            self._sync_type_tool_strip(None)

    def _timeline_tools_allowed(self) -> bool:
        return self._project is not None and self._project.source is not None

    def _apply_edit_type(self, op_type: OperationType) -> None:
        if not self._timeline_tools_allowed() or self._focus_blocks_edit_shortcut():
            return
        self._timeline.set_draw_operation_type(op_type)
        op_id = self._timeline.selected_operation_id()
        if op_id is None:
            op_id = self._operations_panel.current_operation_id()
        op = self._find_operation(op_id)
        if op is None:
            self._sync_type_tool_strip(op_type)
            return
        if op.type == op_type:
            self._sync_type_tool_strip(op_type)
            return
        self._on_censor_params_changed(op_id, op_type, op.censor_params())

    def _on_timeline_region_selected(self, op_id: str) -> None:
        self._select_spatial_region()
        self._operations_panel.select_operation(op_id, emit_selection_changed=False)
        self._timeline.select_operation(op_id)
        if self._project is None:
            return
        for op in self._project.operations:
            if op.id == op_id:
                self._operations_panel.update_operation_editor(op)
                endpoint = self._timeline.active_trim_endpoint()
                if endpoint:
                    self._sync_video_region_overlay(op, endpoint=endpoint)
                else:
                    self._sync_video_region_overlay(op)
                self._sync_type_tool_strip(op.type)
                break
        self._sync_beep_tone_for_selection()

    def _on_timeline_region_deselected(self) -> None:
        self._select_spatial_region()
        self._operations_panel.clear_selection()
        self._timeline_panel.set_beep_tone_for_operation(None)
        self._preview_panel.clear_video_region()
        self._sync_type_tool_strip(None)

    def _on_operation_params_changed(
        self,
        op_id: str,
        params: CensorParams,
        final: bool,
    ) -> None:
        if self._project is None:
            return
        self._begin_op_gesture(op_id)
        updated = self._find_operation(op_id)
        if updated is None:
            return
        updated.apply_censor_params(params)
        # Gain drags emit on every move. Hold the graph until the gesture pauses.
        self._sync_preview_filters(lavfi_delay_ms=0 if final else LAVFI_DRAG_DEBOUNCE_MS)
        if final:
            self._operations_panel.refresh_row(updated)
            self._sync_beep_tone_for_selection()
            self._notify_project_modified()
            self._end_op_gesture(op_id, "adjust region")

    def _refresh_suggestions_panel(self) -> None:
        if self._project is None:
            self._suggestions_panel.load_suggestions([])
            self._timeline.sync_suggestions([])
            return
        suggestions = self._project.suggestions_for_selected_track()
        self._suggestions_panel.load_suggestions(suggestions)
        pending = [s for s in suggestions if s.status == SuggestionStatus.PENDING]
        self._timeline.sync_suggestions(pending)

    def _load_operations_into_panel(self, operations: list[Operation]) -> None:
        project = self._project
        track = project.selected_track() if project is not None else None
        languages = (
            {item.stream_index: project.track_language(item) for item in project.tracks}
            if project is not None
            else {}
        )
        self._operations_panel.load_operations(
            operations,
            strategy=track.dialogue_target_strategy if track else None,
            channel_layout=track.channel_layout if track else None,
            channels=track.channels if track else 0,
            track_languages=languages,
        )

    def _refresh_operations_panel(
        self,
        *,
        sync_timeline: bool = True,
        sync_preview: bool = True,
    ) -> None:
        if self._project is None:
            return
        ops = self._project.timeline_operations()
        self._load_operations_into_panel(ops)
        if sync_timeline:
            self._timeline.sync_operations(ops)
        if sync_preview:
            self._sync_preview_filters()
        self._sync_audio_track_button()

    def _sync_preview_filters(self, *, lavfi_delay_ms: int = 0) -> None:
        if self._project is None:
            return
        if self._project.selected_track() is None:
            return
        if getattr(self, "_preview_sync_suppressed", 0) > 0:
            return
        # Apply lavfi immediately — never block preview on matte ffmpeg extracts.
        # ``lavfi_delay_ms`` coalesces graph swaps while a drag is in progress.
        self._preview_panel.sync_track_and_filters(lavfi_delay_ms=lavfi_delay_ms)
        if self._project.preserve_letterbox_on_zoom and self._project_has_zoom_ops():
            self._schedule_deferred(0, self._ensure_video_matte_and_refresh)

    def _on_operation_created(self, type_name: str, start_s: float, end_s: float) -> None:
        if self._project is None:
            return
        track = self._project.selected_track()
        if track is None:
            return
        rate = track.sample_rate or 48000
        op = Operation.create(
            normalize_operation_type(type_name),
            track.stream_index,
            self._project.default_channels_for_selected(),
            TimePoint.from_seconds_float(start_s, rate),
            TimePoint.from_seconds_float(end_s, rate),
            order=len(self._project.operations),
            language=self._project.track_language(track)[0],
        )
        index = len(self._project.operations)
        self._project.operations.append(op)
        self._refresh_operations_panel()
        self._operations_panel.select_operation(op.id, emit_selection_changed=False)
        self._timeline.select_operation(op.id)
        self._select_spatial_region()
        self._sync_beep_tone_for_selection()
        self._sync_video_region_overlay(op, endpoint="start")
        self._sync_type_tool_strip(op.type)
        self._regenerate_subtitles_for_operations()
        self._notify_project_modified()
        self._push_undo(
            "create region",
            undo=lambda: self._undo_remove_operation(op.id),
            redo=lambda: self._undo_add_operation(op, index),
        )

    def _on_operation_moved(self, op_id: str, start_s: float, end_s: float, final: bool) -> None:
        if self._project is None:
            return
        track = self._project.selected_track()
        if track is None:
            return
        self._begin_op_gesture(op_id)
        rate = track.sample_rate or 48000
        for op in self._project.operations:
            if op.id == op_id:
                old_start = float(op.time_range.start.seconds)
                old_end = float(op.time_range.end.seconds)
                op.time_range.start = TimePoint.from_seconds_float(start_s, rate)
                op.time_range.end = TimePoint.from_seconds_float(end_s, rate)
                retarget_spatial_keyframes(op, old_start, old_end, start_s, end_s)
                break
        self._timeline.select_operation(op_id)
        self._select_spatial_region()
        if final:
            self._load_operations_into_panel(self._project.timeline_operations())
            self._operations_panel.select_operation(op_id, emit_selection_changed=False)
            self._timeline.select_operation(op_id)
            moved = self._find_operation(op_id)
            if moved is not None:
                self._sync_video_region_overlay(moved)
            # Rebuild lavfi after the handle seek settles; doing it mid-scrub
            # blanks the paused frame.
            if getattr(self, "_timeline_scrub_active", False):
                self._pending_preview_sync_after_scrub = True
            else:
                self._sync_preview_filters()
            self._regenerate_subtitles_for_operations()
            self._notify_project_modified()
            self._end_op_gesture(op_id, "move region")

    def _delete_operation(self, op_id: str) -> None:
        if self._project is None:
            return
        removed = self._find_operation(op_id)
        if removed is None:
            return
        index = self._project.operations.index(removed)
        self._project.operations = [o for o in self._project.operations if o.id != op_id]
        was_selected = self._timeline.selected_operation_id() == op_id
        self._refresh_operations_panel()
        if was_selected:
            self._timeline.clear_operation_selection()
            self._operations_panel.clear_selection()
            self._preview_panel.clear_video_region()
            self._sync_type_tool_strip(None)
        self._regenerate_subtitles_for_operations()
        self._notify_project_modified()
        self._push_undo(
            "delete region",
            undo=lambda: self._undo_add_operation(removed, index),
            redo=lambda: self._undo_remove_operation(op_id),
        )

    def _on_op_selection(self, op_id: str) -> None:
        if self._project is None:
            return
        self._select_spatial_region()
        self._timeline.select_operation(op_id)
        for op in self._project.operations:
            if op.id == op_id:
                self._operations_panel.update_operation_editor(op)
                start_s = float(op.time_range.start.seconds)
                self._timeline.go_to_time(start_s, scroll_into_view=True)
                self._sync_beep_tone_for_selection()
                self._sync_video_region_overlay(op, endpoint="start")
                self._sync_type_tool_strip(op.type)
                return
        self._preview_panel.clear_video_region()
        self._sync_type_tool_strip(None)

    def _sync_video_region_overlay(
        self,
        op: Operation | None,
        *,
        endpoint: str | None = None,
        keyframe_index: int | None = None,
    ) -> None:
        if op is None or not is_spatial_operation(op.type):
            self._preview_panel.clear_keyframe_track()
            if op is not None and op.type == OperationType.BLANK:
                self._preview_panel.set_video_region(
                    None,
                    full_frame=True,
                    interactive=False,
                    **self._video_size_kwargs(),
                )
                return
            self._preview_panel.clear_video_region()
            return
        kfs = spatial_keyframes(op)
        if not kfs:
            start = dict(DEFAULT_BOX)
            kfs = [SpatialKeyframe(float(op.time_range.start.seconds), start)]
        animate = has_spatial_animation(op)
        if keyframe_index is not None:
            index = keyframe_index
        elif endpoint == "end":
            index = max(0, len(kfs) - 1)
        elif endpoint == "start":
            index = 0
        else:
            index = self._preview_panel.video_region_keyframe_index()
        if not animate:
            index = 0
        index = max(0, min(index, len(kfs) - 1))
        playhead = self._playhead_keyframe_seconds(op)
        if keyframe_index is None and endpoint is None:
            near = next(
                (i for i, kf in enumerate(kfs) if abs(kf.seconds - playhead) <= _PLAYHEAD_KEY_SNAP),
                None,
            )
            if near is not None:
                index = near
        active = preview_box_at_seconds(kfs, playhead)
        if any(abs(kf.seconds - playhead) <= _PLAYHEAD_KEY_SNAP for kf in kfs):
            active = sample_box_at_seconds(kfs, playhead)
        ghosts = neighbor_pose_boxes(kfs, playhead, snap=_PLAYHEAD_KEY_SNAP)
        path = [copy_box(kf.box) for kf in kfs]
        start_s = float(op.time_range.start.seconds)
        end_s = float(op.time_range.end.seconds)
        duration = max(end_s - start_s, 1e-9)
        self._preview_panel.set_video_region(
            active,
            interactive=True,
            ghosts=ghosts,
            path=path,
            endpoint="end" if animate and index == len(kfs) - 1 else "start",
            keyframe_index=index,
            keyframe_count=len(kfs),
            keyframe_offsets=[(kf.seconds - start_s) / duration for kf in kfs],
            lock_source_aspect=op.type == OperationType.ZOOM,
            **self._video_size_kwargs(),
        )
        syncer = getattr(self._timeline, "sync_pose_markers", None)
        if callable(syncer):
            key_picked = animate and getattr(self, "_spatial_selection", "region") == "keyframe"
            syncer(op, index if key_picked else -1)
        self._refresh_keyframe_track(op, index)
        self._preview_panel.schedule_video_region_relayout()

    def _refresh_keyframe_track(self, op: Operation, index: int) -> None:
        kfs = spatial_keyframes(op)
        if not kfs:
            start = float(op.time_range.start.seconds)
            times = [start]
            index = 0
        else:
            times = [kf.seconds for kf in kfs]
        playhead = float(self._timeline.playhead_seconds())
        selected = index if getattr(self, "_spatial_selection", "region") == "keyframe" else -1
        self._preview_panel.set_keyframe_track(
            float(op.time_range.start.seconds),
            float(op.time_range.end.seconds),
            times,
            index,
            playhead=playhead,
            selected=selected,
        )

    def _video_size_kwargs(self) -> dict[str, int | None]:
        if self._project is None or self._project.source is None:
            return {"video_width": None, "video_height": None}
        probe = self._project.source.probe or {}
        streams = probe.get("video_streams") if isinstance(probe, dict) else None
        if isinstance(streams, list) and streams and isinstance(streams[0], dict):
            return {
                "video_width": streams[0].get("width"),
                "video_height": streams[0].get("height"),
            }
        return {"video_width": None, "video_height": None}

    def _seek_to_spatial_keyframe(self, op: Operation, index: int) -> None:
        seconds = keyframe_seconds(op, index)
        ender = getattr(self, "_end_timeline_scrub", None)
        if callable(ender):
            ender(restore_seek=False)
        self._timeline.set_playhead(seconds, notify=False)
        self._timeline.scroll_playhead_into_view()
        self._preview_panel.seek_exact(seconds)

    def _seek_to_spatial_endpoint(self, op: Operation, endpoint: str) -> None:
        kfs = spatial_keyframes(op)
        index = 0 if endpoint != "end" else max(0, len(kfs) - 1)
        self._seek_to_spatial_keyframe(op, index)

    def _on_video_region_box_changed(self, box: dict) -> None:
        op_id = self._timeline.selected_operation_id()
        op = self._find_operation(op_id)
        if op is None or not is_spatial_operation(op.type):
            return
        kfs = list(spatial_keyframes(op))
        playhead = self._playhead_keyframe_seconds(op)
        if not kfs:
            kfs = [SpatialKeyframe(playhead, copy_box(box))]
            set_spatial_keyframes(op, kfs)
            index = 0
        else:
            near = next(
                (i for i, kf in enumerate(kfs) if abs(kf.seconds - playhead) <= _PLAYHEAD_KEY_SNAP),
                None,
            )
            if near is not None:
                kfs[near] = SpatialKeyframe(kfs[near].seconds, copy_box(box))
                set_spatial_keyframes(op, kfs)
                index = near
            else:
                index = add_spatial_keyframe(op, playhead, box)
        self._select_spatial_keyframe(index)
        self._preview_panel.set_video_region_keyframe_index(index)
        dragging = self._preview_panel.is_video_region_dragging
        if dragging:
            self._refresh_keyframe_track(op, index)
        else:
            self._sync_video_region_overlay(op, keyframe_index=index)
        self._notify_project_modified()
        self._schedule_video_box_preview_sync()

    def _schedule_video_box_preview_sync(self) -> None:
        """Debounce lavfi rebuild while the overlay box is dragged."""
        timer = getattr(self, "_video_box_preview_timer", None)
        if timer is None:
            timer = QTimer(self)
            timer.setSingleShot(True)
            timer.timeout.connect(self._sync_preview_filters)
            self._video_box_preview_timer = timer
        timer.start(50)

    def _on_operation_meta_changed(self, op_id: str) -> None:
        self._notify_project_modified()
        op = self._find_operation(op_id)
        if op is None:
            return
        self._sync_video_region_overlay(op)
        # Radius / size / scale change the lavfi graph for video ops.
        if op.type in {
            OperationType.BLUR,
            OperationType.PIXELATE,
            OperationType.ZOOM,
            OperationType.CROP,
            OperationType.COVER,
            OperationType.BLANK,
        }:
            self._schedule_video_box_preview_sync()

    def _on_box_endpoint_changed(self, op_id: str, endpoint: str) -> None:
        op = self._find_operation(op_id)
        if op is not None:
            self._seek_to_spatial_endpoint(op, endpoint)
            self._sync_video_region_overlay(op, endpoint=endpoint)

    def _on_video_region_endpoint_changed(self, endpoint: str) -> None:
        if endpoint not in {"start", "end"}:
            return
        op = self._find_operation(self._timeline.selected_operation_id())
        if op is None or not is_spatial_operation(op.type):
            return
        self._seek_to_spatial_endpoint(op, endpoint)
        self._sync_video_region_overlay(op, endpoint=endpoint)

    def _on_video_region_keyframe_index_changed(self, index: int) -> None:
        op = self._find_operation(self._timeline.selected_operation_id())
        if op is None or not is_spatial_operation(op.type):
            return
        self._select_spatial_keyframe(index)
        self._seek_to_spatial_keyframe(op, index)
        self._sync_video_region_overlay(op, keyframe_index=index)

    def _playhead_keyframe_seconds(self, _op: Operation) -> float:
        return max(0.0, float(self._timeline.playhead_seconds()))

    def _on_timeline_pose_selected(self, op_id: str, index: int) -> None:
        op = self._find_operation(op_id)
        if op is None or not is_spatial_operation(op.type):
            return
        self._seek_to_spatial_keyframe(op, index)
        self._sync_video_region_overlay(op, keyframe_index=index)

    def _on_video_region_add_keyframe(self, seconds: float | None = None) -> None:
        op = self._find_operation(self._timeline.selected_operation_id())
        if op is None or not is_spatial_operation(op.type):
            return
        before = _OpFields.capture(op)
        at = float(seconds) if seconds is not None else self._playhead_keyframe_seconds(op)
        index = add_spatial_keyframe(op, at)
        self._select_spatial_keyframe(index)
        self._notify_project_modified()
        self._sync_preview_filters()
        self._sync_video_region_overlay(op, keyframe_index=index)
        self._seek_to_spatial_keyframe(op, index)
        self._commit_op_change(op.id, before, "add keyframe")

    def _on_keyframe_track_seek(self, seconds: float) -> None:
        self._track_keyframe_playhead(seconds)
        self._preview_panel.queue_keyframe_scrub_seek(seconds)

    def _on_keyframe_track_seek_finished(self, seconds: float) -> None:
        self._track_keyframe_playhead(seconds)
        self._preview_panel.finish_keyframe_scrub_seek(seconds)
        self._preview_panel.schedule_video_region_relayout()

    def _track_keyframe_playhead(self, seconds: float) -> None:
        if getattr(self, "_timeline_scrub_active", False):
            ender = getattr(self, "_end_timeline_scrub", None)
            if callable(ender):
                ender(restore_seek=False)
        self._timeline.set_playhead(seconds, notify=False)
        self._timeline.scroll_playhead_into_view()
        self._follow_spatial_overlay(seconds)

    def _on_keyframe_track_background_pressed(self) -> None:
        self._select_spatial_region()
        op = self._find_operation(self._timeline.selected_operation_id())
        if op is None or not is_spatial_operation(op.type):
            return
        self._refresh_keyframe_track(op, self._preview_panel.video_region_keyframe_index())

    def _follow_spatial_overlay(self, seconds: float) -> None:
        """Keep the overlay box on the interpolated pose at *seconds*."""
        if self._preview_panel.is_video_region_dragging:
            return
        op = self._find_operation(self._timeline.selected_operation_id())
        if op is None or not is_spatial_operation(op.type):
            return
        kfs = spatial_keyframes(op)
        if not kfs:
            return
        if any(abs(kf.seconds - seconds) <= _PLAYHEAD_KEY_SNAP for kf in kfs):
            box = sample_box_at_seconds(kfs, seconds)
        else:
            box = preview_box_at_seconds(kfs, seconds)
        self._preview_panel.set_video_region_sampled_box(
            box,
            ghosts=neighbor_pose_boxes(kfs, seconds, snap=_PLAYHEAD_KEY_SNAP),
        )
        self._preview_panel.set_keyframe_playhead(seconds)

    def _try_remove_selected_keyframe(self) -> bool:
        op = self._find_operation(self._timeline.selected_operation_id())
        if op is None or not is_spatial_operation(op.type):
            return False
        if getattr(self, "_spatial_selection", "region") != "keyframe":
            return False
        if len(spatial_keyframes(op)) <= 1:
            return False
        self._on_video_region_remove_keyframe()
        return True

    def _on_video_region_key_moved(self, index: int, seconds: float) -> None:
        op = self._find_operation(self._timeline.selected_operation_id())
        if op is None or not is_spatial_operation(op.type):
            return
        kfs = spatial_keyframes(op)
        if 0 <= index < len(kfs) and abs(kfs[index].seconds - seconds) <= _PLAYHEAD_KEY_SNAP:
            return
        before = _OpFields.capture(op)
        index = move_spatial_keyframe(op, index, seconds)
        self._select_spatial_keyframe(index)
        self._notify_project_modified()
        self._sync_preview_filters()
        self._sync_video_region_overlay(op, keyframe_index=index)
        self._seek_to_spatial_keyframe(op, index)
        self._commit_op_change(op.id, before, "move keyframe")

    def _on_video_region_remove_keyframe(self) -> None:
        op = self._find_operation(self._timeline.selected_operation_id())
        if op is None or not is_spatial_operation(op.type):
            return
        selected = self._preview_panel.keyframe_selected_index()
        index = selected if selected >= 0 else self._preview_panel.video_region_keyframe_index()
        kfs = spatial_keyframes(op)
        if len(kfs) <= 1:
            return
        before = _OpFields.capture(op)
        index = remove_spatial_keyframe(op, index)
        self._select_spatial_keyframe(index)
        self._notify_project_modified()
        self._sync_preview_filters()
        self._sync_video_region_overlay(op, keyframe_index=index)
        self._seek_to_spatial_keyframe(op, index)
        self._commit_op_change(op.id, before, "remove keyframe")

    def _on_suggestion_seek(self, seconds: float) -> None:
        self._timeline.go_to_time(seconds, scroll_into_view=True)

    def _on_beep_tone_changed(self, freq: float) -> None:
        op_id = self._timeline.selected_operation_id()
        op = self._find_operation(op_id)
        if op is None or op.type != OperationType.BEEP:
            return
        params = op.censor_params().with_beep_freq(freq)
        self._on_censor_params_changed(op_id, OperationType.BEEP, params)

    def _on_censor_params_changed(
        self,
        op_id: str,
        op_type: OperationType,
        params: CensorParams,
    ) -> None:
        if self._project is None:
            return
        target = self._find_operation(op_id)
        if target is None:
            return
        type_changed = target.type != op_type
        before = _OpFields.capture(target)
        target.type = op_type
        target.apply_censor_params(params)
        if type_changed:
            self._normalize_operation_after_type_change(target)
        updated = target
        if type_changed:
            self._refresh_operations_panel()
            self._operations_panel.select_operation(op_id, emit_selection_changed=False)
            self._timeline.select_operation(op_id)
            self._operations_panel.update_operation_editor(updated)
            self._sync_video_region_overlay(updated)
            self._sync_preview_filters()
            self._sync_type_tool_strip(op_type)
        else:
            self._timeline.update_operation_params(op_id, op_type, updated.censor_params())
            self._operations_panel.refresh_row(updated)
            self._sync_preview_filters()
        self._sync_beep_tone_for_selection()
        self._regenerate_subtitles_for_operations()
        self._notify_project_modified()
        self._commit_op_change(op_id, before, "edit region")

    def _normalize_operation_after_type_change(self, op: Operation) -> None:
        """Ensure scope/params match the new action (e.g. mute → cover gets a box)."""
        if is_item_level_operation(op.type):
            op.scope = OperationScope(stream_index=ITEM_LEVEL_STREAM_INDEX, channels=[])
            op.language = None
        elif is_audio_operation(op.type) and self._project is not None:
            track = self._project.selected_track()
            if track is not None and op.scope.stream_index == ITEM_LEVEL_STREAM_INDEX:
                op.scope = OperationScope(
                    stream_index=track.stream_index,
                    channels=list(self._project.default_channels_for_selected()),
                )
                op.language = self._project.track_language(track)[0]
        defaults = default_params(op.type)
        for key, value in defaults.items():
            if key not in op.params:
                op.params[key] = dict(value) if isinstance(value, dict) else value
        if is_spatial_operation(op.type) and not isinstance(op.params.get("box"), dict):
            if op.type in {OperationType.ZOOM, OperationType.CROP}:
                pass
            else:
                op.params["box"] = dict(DEFAULT_BOX)

    def _on_channels_changed(self, op_id: str, channels: list[str]) -> None:
        if self._project is None:
            return
        target = self._find_operation(op_id)
        if target is None:
            return
        before = _OpFields.capture(target)
        track = self._project.selected_track()
        if track is None:
            target.scope.channels = list(channels)
        else:
            from corrcut.domain.channel_layout import replace_layout_channels

            target.scope.channels = replace_layout_channels(
                target.scope.channels,
                list(channels),
                track.channel_layout,
                track.channels,
            )
        self._sync_preview_filters()
        self._notify_project_modified()
        self._commit_op_change(op_id, before, "change channels")

    def _on_enabled_changed(self, op_id: str, enabled: bool) -> None:
        if self._project is None:
            return
        target = self._find_operation(op_id)
        if target is None:
            return
        before = _OpFields.capture(target)
        before.enabled = not enabled
        target.enabled = enabled
        self._operations_panel.refresh_row(target)
        self._timeline.sync_operations(self._project.timeline_operations())
        self._sync_preview_filters()
        self._notify_project_modified()
        self._commit_op_change(op_id, before, "enable edit" if enabled else "disable edit")

    def _sync_audio_track_button(self) -> None:
        """Rebuild the timeline audio menu after the current event finishes."""
        if getattr(self, "_audio_menu_sync_queued", False):
            return
        self._audio_menu_sync_queued = True
        QTimer.singleShot(0, self._flush_audio_track_button)

    def _flush_audio_track_button(self) -> None:
        self._audio_menu_sync_queued = False
        from corrcut.domain.audio_language import (
            LANGUAGE_CHOICES,
            language_display_name,
            track_choice_label,
        )

        project = self._project
        panel = self._timeline_panel
        if project is None or not project.tracks:
            panel.set_audio_track_choices(
                [],
                None,
                "No audio",
                [],
                "",
                override_set=False,
            )
            return
        current = project.selected_track()
        entries: list[tuple[int, str]] = []
        for track in project.tracks:
            code, assumed = project.track_language(track)
            count = sum(1 for op in project.operations if project.audio_op_matches_track(op, track))
            layout = track.channel_layout or f"{track.channels} ch"
            entries.append(
                (
                    track.stream_index,
                    track_choice_label(
                        language_name=language_display_name(code, assumed=assumed),
                        layout=layout,
                        title=track.title,
                        edit_count=count,
                    ),
                )
            )
        if current is None:
            button = "Audio"
            code = ""
            override = False
        else:
            code, assumed = project.track_language(current)
            layout = current.channel_layout or f"{current.channels} ch"
            button = f"{language_display_name(code, assumed=assumed)} · {layout}"
            override = str(current.stream_index) in project.audio_language_overrides
        panel.set_audio_track_choices(
            entries,
            None if current is None else current.stream_index,
            button,
            list(LANGUAGE_CHOICES),
            code,
            override_set=override,
        )

    def _on_audio_language_chosen(self, code: str) -> None:
        if self._project is None:
            return
        track = self._project.selected_track()
        if track is None:
            return
        project = self._project
        previous_overrides = dict(project.audio_language_overrides)
        previous_languages = [(op.id, op.language) for op in project.operations]
        project.set_track_language(track.stream_index, code or None)
        self._refresh_operations_panel()
        self._notify_project_modified()

        def _restore() -> None:
            project.audio_language_overrides.clear()
            project.audio_language_overrides.update(previous_overrides)
            by_id = {op.id: op for op in project.operations}
            for op_id, language in previous_languages:
                op = by_id.get(op_id)
                if op is not None:
                    op.language = language
            self._refresh_operations_panel()

        def _apply() -> None:
            project.set_track_language(track.stream_index, code or None)
            self._refresh_operations_panel()

        self._push_undo("set audio language", _restore, _apply)

    def _on_track_selected(self, stream_index: int) -> None:
        if self._project is None:
            return
        if getattr(self, "_preview_sync_suppressed", 0) > 0:
            return
        if self._project.selected_audio_stream_index == stream_index:
            return
        self._project.selected_audio_stream_index = stream_index
        self._peak_preload_token += 1
        self._waveform_generation += 1
        self._waveform_job_runner.cancel_active()
        track = self._project.selected_track()
        if track is not None:
            self._timeline.set_sample_rate(track.sample_rate or 48000)
        self._timeline.sync_operations(self._project.timeline_operations())
        self._refresh_operations_panel()
        self._refresh_suggestions_panel()
        self._ensure_waveform()
        if getattr(self, "_preview_sync_suppressed", 0) == 0:
            self._preview_panel.sync_track_and_filters()
        self._stream_inspector.select_audio_stream(stream_index)
        self._sync_audio_track_button()
        self._notify_project_modified()

    def _find_suggestion(self, suggestion_id: str) -> AiSuggestion | None:
        if self._project is None:
            return None
        for suggestion in self._project.ai_suggestions:
            if suggestion.id == suggestion_id:
                return suggestion
        return None

    def _accept_suggestion(self, suggestion_id: str) -> None:
        if self._project is None:
            return
        suggestion = self._find_suggestion(suggestion_id)
        if suggestion is None or suggestion.status == SuggestionStatus.ACCEPTED:
            return
        self._run_suggestion_command(
            "accept suggestion",
            lambda: apply_suggestion(self._project, suggestion),
        )

    def _reject_suggestion(self, suggestion_id: str) -> None:
        if self._project is None:
            return
        suggestion = self._find_suggestion(suggestion_id)
        if suggestion is None or suggestion.status == SuggestionStatus.REJECTED:
            return

        def _mutate() -> None:
            if suggestion.status == SuggestionStatus.ACCEPTED:
                revoke_suggestion(self._project, suggestion)
            else:
                suggestion.status = SuggestionStatus.REJECTED

        self._run_suggestion_command("reject suggestion", _mutate)

    def _reset_suggestion_to_pending(self, suggestion_id: str) -> None:
        if self._project is None:
            return
        suggestion = self._find_suggestion(suggestion_id)
        if suggestion is None or suggestion.status == SuggestionStatus.PENDING:
            return
        self._run_suggestion_command(
            "reset suggestion",
            lambda: reset_suggestion_to_pending(self._project, suggestion),
        )
