"""Open-media job shows the project before subtitle extraction."""

from __future__ import annotations

from pathlib import Path

import pytest

from corrcut.domain.project import Project, SourceFileRecord
from corrcut.domain.subtitle_cue import SubtitleCue, SubtitleSource
from corrcut.domain.time_point import TimePoint
from corrcut.engine.subtitles.ingest import EmbeddedSubtitleExtract
from corrcut.io.media_open import PreparedMediaOpen
from corrcut.ui.export_progress_dialog import ExportProgressDialog


def test_scene_cut_job_uses_software_decode(
    editor_window,  # type: ignore[no-untyped-def]
    qtbot,  # type: ignore[no-untyped-def]
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    media = tmp_path / "clip.mkv"
    media.write_bytes(b"x")
    editor_window._project = Project(
        source=SourceFileRecord(
            path=media,
            fingerprint={"size": 1, "head_sha256": "a", "tail_sha256": ""},
            probe={"video_streams": [{"index": 0}], "duration_seconds": 12.0},
        )
    )
    editor_window._duration = 12.0
    seen: dict[str, object] = {}

    def _fake_detect(*_args: object, **kwargs: object) -> list[float]:
        seen["software"] = kwargs.get("software")
        return []

    monkeypatch.setattr(
        "corrcut.ui.editor.scene_cuts.detect_scene_cuts",
        _fake_detect,
    )
    editor_window._start_scene_cut_job()
    qtbot.waitUntil(lambda: "software" in seen, timeout=3000)
    assert seen["software"] is True


def test_ensure_scene_cuts_waits_for_waveform_gate(
    editor_window,  # type: ignore[no-untyped-def]
    qtbot,  # type: ignore[no-untyped-def]
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    media = tmp_path / "clip.mkv"
    media.write_bytes(b"x")
    editor_window._project = Project(
        source=SourceFileRecord(
            path=media,
            fingerprint={"size": 1, "head_sha256": "a", "tail_sha256": ""},
            probe={"video_streams": [{"index": 0}], "duration_seconds": 12.0},
        )
    )
    editor_window._waveform_gate_open_for_scene_cuts = False
    editor_window._scene_cuts_waiting_for_waveform = False
    started: list[str] = []
    monkeypatch.setattr(
        editor_window,
        "_start_scene_cut_job",
        lambda: started.append("start"),
    )

    editor_window._ensure_scene_cuts()
    assert started == []
    assert editor_window._scene_cuts_waiting_for_waveform is True

    editor_window._open_waveform_gate_for_scene_cuts()
    assert editor_window._waveform_gate_open_for_scene_cuts is True
    assert editor_window._scene_cuts_waiting_for_waveform is False
    qtbot.waitUntil(lambda: started == ["start"], timeout=2000)


def test_apply_prepared_media_starts_subtitle_extract_in_background(
    editor_window,  # type: ignore[no-untyped-def]
    qtbot,  # type: ignore[no-untyped-def]
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    media = tmp_path / "Title.mkv"
    media.write_bytes(b"x")
    project = Project(
        source=SourceFileRecord(
            path=media,
            fingerprint={"size": 1, "head_sha256": "a", "tail_sha256": ""},
            probe={"duration_seconds": 10.0, "video_streams": [], "audio_streams": []},
        )
    )
    prepared = PreparedMediaOpen(
        project=project,
        probe_cache_hit=True,
        corr_path=tmp_path / "Title.corr.json",
        sidecar_loaded=False,
        sidecar_error=None,
    )
    calls: list[str] = []
    monkeypatch.setattr(
        "corrcut.ui.editor.subtitles.read_embedded_subtitle_cues",
        lambda *_args, **_kwargs: calls.append("extract"),
    )
    monkeypatch.setattr(
        editor_window,
        "_begin_embedded_subtitle_extract",
        lambda **_kwargs: calls.append("begin"),
    )
    for name in (
        "_ensure_waveform",
        "_ensure_filmstrip",
        "_ensure_scene_cuts",
        "_ensure_video_matte_and_refresh",
    ):
        monkeypatch.setattr(editor_window, name, lambda: None)

    editor_window._apply_prepared_media(prepared)
    qtbot.waitUntil(lambda: "begin" in calls, timeout=2000)

    assert calls == ["begin"]
    assert editor_window._project is project
    assert editor_window._corr_path == prepared.corr_path
    assert "Loaded Title.mkv" in editor_window._status.currentMessage()


def test_apply_fills_panels_while_dialog_is_open(
    editor_window,  # type: ignore[no-untyped-def]
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    media = tmp_path / "Title.mkv"
    media.write_bytes(b"x")
    project = Project(
        source=SourceFileRecord(
            path=media,
            fingerprint={"size": 1, "head_sha256": "a", "tail_sha256": ""},
            probe={"duration_seconds": 10.0, "video_streams": [], "audio_streams": []},
        )
    )
    prepared = PreparedMediaOpen(
        project=project,
        probe_cache_hit=False,
        corr_path=tmp_path / "Title.corr.json",
        sidecar_loaded=False,
        sidecar_error=None,
    )
    dialog = ExportProgressDialog("Opening Title.mkv", parent=editor_window)
    dialog.show()
    editor_window._media_open_dialog = dialog
    order: list[object] = []
    monkeypatch.setattr(
        editor_window,
        "_refresh_operations_panel",
        lambda **_kwargs: order.append("edits"),
    )
    monkeypatch.setattr(
        editor_window,
        "_load_subtitles_on_idle",
        lambda: order.append("subs"),
    )
    monkeypatch.setattr(
        editor_window,
        "_ensure_video_matte_and_refresh",
        lambda: order.append(("picture", dialog.isVisible())),
    )
    for name in ("_ensure_waveform", "_ensure_filmstrip", "_ensure_scene_cuts"):
        monkeypatch.setattr(editor_window, name, lambda: None)

    editor_window._apply_prepared_media(prepared)

    assert order == ["edits", "subs", ("picture", True)]
    assert editor_window._media_open_dialog is dialog
    editor_window._finish_media_open_dialog()


def test_load_project_file_fills_panels_while_dialog_is_open(
    editor_window,  # type: ignore[no-untyped-def]
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: list[object] = []

    def _fill(dialog: ExportProgressDialog, *, subtitles_ready: bool) -> None:
        seen.append((dialog.isVisible(), subtitles_ready, dialog.windowTitle()))

    monkeypatch.setattr(editor_window, "_fill_project_panels", _fill)
    monkeypatch.setattr(
        "corrcut.ui.editor.project_session.load_project",
        lambda _path: Project(),
    )
    monkeypatch.setattr(
        "corrcut.ui.editor.project_session.add_recent_project",
        lambda _path: None,
    )
    for name in ("_ensure_waveform", "_ensure_filmstrip", "_ensure_scene_cuts"):
        monkeypatch.setattr(editor_window, name, lambda: None)

    assert editor_window.load_project_file(tmp_path / "clip.corrcut") is True
    assert seen == [(True, False, "Open project")]


def _media_project(path: Path) -> Project:
    return Project(
        source=SourceFileRecord(
            path=path,
            fingerprint={"size": 1, "head_sha256": "a", "tail_sha256": ""},
            probe={
                "duration_seconds": 12.0,
                "video_streams": [],
                "audio_streams": [],
                "subtitle_streams": [
                    {"index": 2, "codec": "subrip", "language": "eng", "title": None}
                ],
            },
        )
    )


def _hello_cue() -> SubtitleCue:
    return SubtitleCue.create(
        start=TimePoint.from_seconds_float(1.0, 48_000),
        end=TimePoint.from_seconds_float(2.0, 48_000),
        text="Hello",
        source=SubtitleSource.EMBEDDED,
    )


def test_embedded_extract_fills_panel_without_moving_playhead(
    editor_window,  # type: ignore[no-untyped-def]
    qtbot,  # type: ignore[no-untyped-def]
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    media = tmp_path / "Title.mkv"
    media.write_bytes(b"x")
    editor_window._project = _media_project(media)
    editor_window._timeline.set_duration(12.0)
    editor_window._timeline.set_playhead(4.0, notify=False)
    cue = _hello_cue()
    monkeypatch.setattr(
        "corrcut.ui.editor.subtitles.read_embedded_subtitle_cues",
        lambda *_args, **_kwargs: (2, [cue]),
    )

    editor_window._start_embedded_subtitle_extract()
    status = editor_window._subtitles_panel._extract_status
    assert status.isHidden() is False
    assert "subrip" in status.text()
    assert "eng" in status.text()

    qtbot.waitUntil(
        lambda: bool(editor_window._project.subtitle_cues),
        timeout=3000,
    )

    assert editor_window._timeline.playhead_seconds() == 4.0
    assert editor_window._project.subtitle_cues[0].text == "Hello"
    assert editor_window._subtitles_panel._extract_status.isHidden() is True
    assert "Loaded embedded subtitles" in editor_window._status.currentMessage()


def test_stale_embedded_extract_does_not_replace_cues(
    editor_window,  # type: ignore[no-untyped-def]
    tmp_path: Path,
) -> None:
    media = tmp_path / "Title.mkv"
    media.write_bytes(b"x")
    editor_window._project = _media_project(media)
    editor_window._embedded_extract_active = True
    editor_window._embedded_extract_job_generation = 4
    editor_window._subtitle_extract_generation = 4

    editor_window._on_embedded_extract_result(
        EmbeddedSubtitleExtract(
            generation=2,
            stream_index=2,
            cues=(_hello_cue(),),
        )
    )

    assert editor_window._project.subtitle_cues == []
    assert editor_window._embedded_extract_active is True

