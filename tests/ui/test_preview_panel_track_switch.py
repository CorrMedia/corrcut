from __future__ import annotations

from fractions import Fraction
from pathlib import Path

import pytest
from PySide6.QtWidgets import QApplication

from corrcut.domain.dialogue_target import DialogueTargetStrategy
from corrcut.domain.operation import Operation, OperationType
from corrcut.domain.project import Project, SourceFileRecord, TrackRecord
from corrcut.domain.time_point import TimePoint
from corrcut.preview.playback_profile import PlaybackProfile
from corrcut.preview.skip_playback import skip_ranges_from_project
from corrcut.ui.preview_panel import LAVFI_DRAG_DEBOUNCE_MS, PreviewPanel

# Ensure a Qt application exists for QWidget construction.
_app = QApplication.instance() or QApplication([])


class _FakeBackend:
    """Backend stand-in to simulate mpv time-pos drift on track switch."""

    def __init__(self, *, start_pos: float = 10.5) -> None:
        self._pos = float(start_pos)
        self.seek_calls: list[tuple[float, str]] = []
        self.set_audio_stream_calls: list[tuple[int, int]] = []
        self.set_lavfi_calls: list[str | None] = []

    def position(self) -> float:
        return float(self._pos)

    def set_audio_stream(self, ffmpeg_stream_index: int, *, audio_ordinal: int = 0) -> bool:
        self.set_audio_stream_calls.append((int(ffmpeg_stream_index), int(audio_ordinal)))
        # Simulate mpv nudging time-pos forward during track switch / reinit.
        self._pos += 0.5
        return True

    def set_lavfi_complex(self, graph: str | None) -> None:
        self.set_lavfi_calls.append(graph)
        # Another common place mpv might "settle" on a nearby decode boundary.
        self._pos += 0.0

    def seek(self, seconds: float, *, reference: str = "absolute") -> None:
        self.seek_calls.append((float(seconds), str(reference)))
        self._pos = float(seconds)

    def is_playing(self) -> bool:
        return False


def _project_two_tracks_selected(stream_index: int = 2) -> Project:
    tracks = [
        TrackRecord(
            stream_index=1,
            codec="aac",
            channel_layout="stereo",
            channels=2,
            sample_rate=48000,
            language="eng",
            dialogue_target_strategy=DialogueTargetStrategy.FULL_MIX,
        ),
        TrackRecord(
            stream_index=2,
            codec="aac",
            channel_layout="stereo",
            channels=2,
            sample_rate=48000,
            language="spa",
            dialogue_target_strategy=DialogueTargetStrategy.FULL_MIX,
        ),
    ]
    return Project(
        source=SourceFileRecord(
            path=Path(__file__),
            fingerprint={},
            probe={"duration_seconds": 20.0},
        ),
        tracks=tracks,
        selected_audio_stream_index=stream_index,
        operations=[],
    )


def test_same_lavfi_template_reapplies_when_audio_id_changes() -> None:
    panel = PreviewPanel()
    backend = _FakeBackend()
    backend.selected_audio_id = 2
    panel._backend = backend  # type: ignore[attr-defined]
    panel._project = _project_two_tracks_selected(1)  # type: ignore[attr-defined]

    panel._flush_lavfi_apply()
    assert len(backend.set_lavfi_calls) == 1

    panel._flush_lavfi_apply()
    assert len(backend.set_lavfi_calls) == 1

    backend.selected_audio_id = 4
    panel._project = _project_two_tracks_selected(2)  # type: ignore[attr-defined]
    panel._flush_lavfi_apply()
    assert len(backend.set_lavfi_calls) == 2


def test_track_switch_restores_playhead_time_after_mpv_drift(qtbot) -> None:  # type: ignore[no-untyped-def]
    panel = PreviewPanel()
    backend = _FakeBackend(start_pos=10.5)
    panel._backend = backend  # type: ignore[attr-defined]  # test injection
    panel._project = _project_two_tracks_selected()  # type: ignore[attr-defined]  # test injection

    # Avoid pulling in the full filtergraph compiler; just ensure reload triggers backend wiring.
    def _reload_lavfi_stub() -> None:
        backend.set_lavfi_complex("[aid]anull[ao]")

    panel.reload_lavfi = _reload_lavfi_stub  # type: ignore[method-assign]

    panel.sync_track_and_filters()

    qtbot.waitUntil(lambda: len(backend.seek_calls) >= 1, timeout=1500)
    assert backend.seek_calls[-1] == (10.5, "absolute+exact")


def test_lavfi_reload_debounces_while_a_drag_is_active() -> None:
    panel = PreviewPanel()
    backend = _FakeBackend()
    panel._backend = backend  # type: ignore[attr-defined]
    panel._project = _project_two_tracks_selected()  # type: ignore[attr-defined]

    panel.sync_track_and_filters(lavfi_delay_ms=LAVFI_DRAG_DEBOUNCE_MS)
    assert panel._lavfi_apply_timer.isActive()  # noqa: SLF001
    assert panel._lavfi_apply_timer.interval() == LAVFI_DRAG_DEBOUNCE_MS  # noqa: SLF001
    assert backend.set_lavfi_calls == []
    panel._lavfi_apply_timer.stop()  # noqa: SLF001

    panel._video_region_overlay._drag_mode = "move"  # type: ignore[attr-defined]  # noqa: SLF001
    panel.reload_lavfi()
    assert panel._lavfi_apply_timer.interval() == LAVFI_DRAG_DEBOUNCE_MS  # noqa: SLF001
    panel._lavfi_apply_timer.stop()  # noqa: SLF001

    panel._video_region_overlay._drag_mode = None  # type: ignore[attr-defined]  # noqa: SLF001
    panel.reload_lavfi()
    assert panel._lavfi_apply_timer.interval() == 0  # noqa: SLF001
    panel._lavfi_apply_timer.stop()  # noqa: SLF001


def test_load_settle_blocks_near_start_drift() -> None:
    panel = PreviewPanel()
    emitted: list[float] = []
    panel.position_changed.connect(emitted.append)

    panel._load_settle_generation = 1  # type: ignore[attr-defined]
    panel._relay_position(0.584)
    assert emitted == []

    panel._complete_load_settle(suppress_start_drift=True)  # type: ignore[attr-defined]
    assert emitted == [0.0]

    panel._relay_position(0.584)
    assert emitted == [0.0]

    panel._start_drift_suppress_timer.stop()  # type: ignore[attr-defined]
    panel._relay_position(0.584)
    assert emitted == [0.0, 0.584]


def test_load_settle_retries_seek_until_start() -> None:
    panel = PreviewPanel()
    backend = _FakeBackend(start_pos=0.584)
    panel._backend = backend  # type: ignore[attr-defined]
    panel._sync_generation = 3  # type: ignore[attr-defined]

    emitted: list[float] = []
    panel.position_changed.connect(emitted.append)

    panel._begin_load_settle(3)  # type: ignore[attr-defined]
    for _ in range(100):
        _app.processEvents()
        if panel._load_settle_generation is None:  # type: ignore[attr-defined]
            break

    assert panel._load_settle_generation is None  # type: ignore[attr-defined]
    assert backend.seek_calls
    assert all(call[0] == 0.0 for call in backend.seek_calls)
    assert emitted == [0.0]


def test_seek_during_load_settle_is_queued_until_ready() -> None:
    panel = PreviewPanel()
    backend = _FakeBackend(start_pos=0.0)
    panel._backend = backend  # type: ignore[attr-defined]
    panel._media_path = Path("dummy.mkv")  # type: ignore[attr-defined]
    panel._media_load_completed = True  # type: ignore[attr-defined]
    panel._sync_generation = 4  # type: ignore[attr-defined]
    panel._media_setup_generation = 4  # type: ignore[attr-defined]

    emitted: list[float] = []
    panel.position_changed.connect(emitted.append)

    panel._begin_load_settle(4)  # type: ignore[attr-defined]
    panel.seek(12.5, reference="absolute+exact")
    assert backend.seek_calls == [] or all(call[0] == 0.0 for call in backend.seek_calls)
    assert panel._pending_seek_s == 12.5  # type: ignore[attr-defined]

    for _ in range(100):
        _app.processEvents()
        if panel._load_settle_generation is None:  # type: ignore[attr-defined]
            break

    assert panel._load_settle_generation is None  # type: ignore[attr-defined]
    assert panel._pending_seek_s is None  # type: ignore[attr-defined]
    assert backend.seek_calls[-1] == (12.5, "absolute+exact")
    assert emitted[-1] == 12.5


def _skip(start: float, end: float) -> Operation:
    return Operation.create(
        OperationType.SKIP,
        -1,
        [],
        TimePoint(Fraction(str(start))),
        TimePoint(Fraction(str(end))),
    )


def test_edited_playback_reuses_skip_ranges_until_an_edit(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = 0

    def counting(
        project: Project | None,
        profile: PlaybackProfile | None = None,
    ) -> list[tuple[float, float]]:
        nonlocal calls
        calls += 1
        return skip_ranges_from_project(project, profile=profile)

    monkeypatch.setattr("corrcut.ui.preview_panel.skip_ranges_from_project", counting)

    panel = PreviewPanel()
    project = _project_two_tracks_selected()
    assert project.source is not None
    project.source = SourceFileRecord(
        path=Path("missing-movie.mkv"),
        fingerprint={},
        probe={"duration_seconds": 20.0},
    )
    project.operations = [_skip(5.0, 8.0)]
    panel.set_project(project)
    assert calls == 1
    assert panel._skip_ranges == [(5.0, 8.0)]  # noqa: SLF001

    panel._is_playing = True  # noqa: SLF001
    panel._skip_jumps_enabled = True  # noqa: SLF001
    assert panel._jump_skip_if_needed(1.0) is False  # noqa: SLF001
    assert panel._jump_skip_if_needed(6.0) is True  # noqa: SLF001
    assert panel._skip_aware_target(6.5) == 8.0  # noqa: SLF001
    assert calls == 1

    project.operations.append(_skip(10.0, 12.0))
    panel.sync_track_and_filters()
    assert calls == 2
    assert panel._skip_ranges == [(5.0, 8.0), (10.0, 12.0)]  # noqa: SLF001
    assert panel._jump_skip_if_needed(11.0) is True  # noqa: SLF001
    assert calls == 2

    panel.set_project(None)
    assert panel._skip_ranges == []  # noqa: SLF001
    assert calls == 3


def test_frame_step_inside_skip_does_not_jump_to_the_end() -> None:
    """Arrow keys step through a skip. mpv unpauses for one frame; that is not playback."""
    panel = PreviewPanel()
    project = _project_two_tracks_selected()
    project.source = SourceFileRecord(
        path=Path("missing-movie.mkv"),
        fingerprint={},
        probe={"duration_seconds": 20.0},
    )
    project.operations = [_skip(5.0, 8.0)]
    panel.set_project(project)

    backend = _FakeBackend(start_pos=6.0)

    def frame_step(_delta: int) -> None:
        # mpv reports playing, a time-pos still inside the skip, then pause.
        panel._on_playback_state(True)  # noqa: SLF001
        panel._relay_position(backend.position())  # noqa: SLF001
        panel._on_playback_state(False)  # noqa: SLF001

    backend.frame_step = frame_step  # type: ignore[method-assign]
    panel._backend = backend  # type: ignore[attr-defined]

    panel._is_playing = True  # noqa: SLF001
    panel._skip_jumps_enabled = True  # noqa: SLF001
    panel._relay_position(6.0)  # noqa: SLF001
    assert backend.seek_calls == [(8.0, "absolute+exact")]

    backend.seek_calls.clear()
    backend._pos = 6.05
    panel._is_playing = True  # noqa: SLF001
    panel._skip_jumps_enabled = True  # noqa: SLF001
    panel.frame_step(1)
    assert backend.seek_calls == []
    assert panel._is_playing is False  # noqa: SLF001


def test_sync_track_and_filters_skips_while_media_opening() -> None:
    panel = PreviewPanel()
    backend = _FakeBackend(start_pos=0.0)
    panel._backend = backend  # type: ignore[attr-defined]
    panel._project = _project_two_tracks_selected()  # type: ignore[attr-defined]
    panel._media_path = Path("dummy.mkv")  # type: ignore[attr-defined]
    panel._media_load_completed = False  # type: ignore[attr-defined]

    panel.sync_track_and_filters()
    assert backend.set_audio_stream_calls == []
    assert backend.set_lavfi_calls == []
    assert panel._pending_edits_lavfi is True  # type: ignore[attr-defined]

