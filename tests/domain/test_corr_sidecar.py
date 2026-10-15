"""corr.json sidecar round-trip tests."""

from __future__ import annotations

from pathlib import Path

import pytest

from corrcut.domain.corr_sidecar import (
    load_corr_file,
    operations_from_corr_document,
    operations_to_corr_document,
    save_corr_file,
    sidecar_path,
)
from corrcut.domain.operation import (
    ITEM_LEVEL_STREAM_INDEX,
    Operation,
    OperationType,
    duck_db_to_gain,
)
from corrcut.domain.project import Project
from corrcut.domain.spatial_keyframes import (
    SpatialKeyframe,
    set_spatial_keyframes,
    spatial_keyframes,
)
from corrcut.domain.time_point import TimePoint
from corrcut.domain.video_matte import VideoMatteCache

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "corr"


def test_sidecar_path() -> None:
    assert sidecar_path(Path("movies/Title.mkv")).name == "Title.corr.json"


def test_round_trip_test_movie_fixture() -> None:
    data = load_corr_file(FIXTURES / "test-movie.corr.json")
    ops = operations_from_corr_document(data, default_stream_index=1)
    actions = {op.type for op in ops}
    assert OperationType.MUTE in actions
    assert OperationType.VOLUME in actions
    assert OperationType.BEEP in actions
    assert OperationType.SKIP in actions
    assert OperationType.COVER in actions
    assert OperationType.CROP in actions
    assert OperationType.ZOOM in actions
    assert OperationType.PIXELATE in actions
    assert OperationType.BLANK in actions
    assert OperationType.BLUR in actions
    center_mute = next(
        op for op in ops if op.type == OperationType.MUTE and op.scope.channels == ["FC"]
    )
    assert center_mute.categories == ["racial_slurs"]
    assert center_mute.severity == 1
    zoom = next(op for op in ops if op.type == OperationType.ZOOM)
    assert zoom.params.get("x_end") == 0.7
    cover = next(op for op in ops if op.type == OperationType.COVER)
    assert cover.params.get("box_end") is not None

    project = Project(operations=ops, selected_audio_stream_index=1)
    out = operations_to_corr_document(project)
    for edit in out["edits"]:
        assert edit["categories"]
        assert all(isinstance(c, str) and c.strip() for c in edit["categories"])
        if edit["id"] != "edit_006":
            assert edit["severity"] == 1
    again = operations_from_corr_document(out, default_stream_index=1)
    assert len(again) == len(ops)
    assert {op.type for op in again} == actions


def test_ignores_unknown_and_inverted() -> None:
    data = {
        "schema_version": "1.0",
        "edits": [
            {"id": "ok", "start": 1, "end": 2, "action": "mute"},
            {"id": "poi", "start": 3, "end": 4, "action": "poi"},
            {"id": "bad", "start": 9, "end": 8, "action": "skip"},
        ],
    }
    ops = operations_from_corr_document(data, default_stream_index=0)
    assert len(ops) == 1
    assert ops[0].id == "ok"
    assert ops[0].categories == ["other"]
    assert ops[0].severity is None


def test_severity_round_trips_and_omits_when_unset() -> None:
    data = {
        "schema_version": "1.0",
        "edits": [
            {"id": "rated", "start": 1, "end": 2, "action": "mute", "severity": 3},
            {"id": "plain", "start": 3, "end": 4, "action": "skip"},
        ],
    }
    ops = operations_from_corr_document(data, default_stream_index=0)
    assert ops[0].severity == 3
    assert ops[1].severity is None
    project = Project(operations=ops, selected_audio_stream_index=0)
    out = operations_to_corr_document(project)
    by_id = {edit["id"]: edit for edit in out["edits"]}
    assert by_id["rated"]["severity"] == 3
    assert "severity" not in by_id["plain"]


def test_duck_db_migrates_to_volume_gain() -> None:
    assert abs(duck_db_to_gain(-20.0) - 0.1) < 1e-6


def test_item_level_ops_on_timeline() -> None:
    from fractions import Fraction

    from corrcut.domain.operation import Operation
    from corrcut.domain.time_point import TimePoint

    project = Project(selected_audio_stream_index=2)
    project.operations = [
        Operation.create(
            OperationType.MUTE,
            2,
            ["FC"],
            TimePoint(Fraction(1)),
            TimePoint(Fraction(2)),
        ),
        Operation.create(
            OperationType.SKIP,
            2,
            [],
            TimePoint(Fraction(3)),
            TimePoint(Fraction(4)),
        ),
    ]
    assert len(project.operations_for_selected_track()) == 1
    assert len(project.timeline_operations()) == 2


def test_disabled_ops_stay_on_timeline_but_not_in_preview_scope() -> None:
    from fractions import Fraction

    from corrcut.domain.operation import Operation
    from corrcut.domain.time_point import TimePoint

    project = Project(selected_audio_stream_index=2)
    mute = Operation.create(
        OperationType.MUTE,
        2,
        ["FC"],
        TimePoint(Fraction(1)),
        TimePoint(Fraction(2)),
    )
    mute.enabled = False
    skip = Operation.create(
        OperationType.SKIP,
        2,
        [],
        TimePoint(Fraction(3)),
        TimePoint(Fraction(4)),
    )
    skip.enabled = False
    project.operations = [mute, skip]
    assert project.operations_for_selected_track() == []
    assert {op.id for op in project.timeline_operations()} == {mute.id, skip.id}


def test_keyframed_cover_exports_abutting_edits_and_reloads() -> None:
    op = Operation.create(
        OperationType.COVER,
        -1,
        [],
        TimePoint.from_seconds_float(10.0, 48000),
        TimePoint.from_seconds_float(20.0, 48000),
        params={"box": {"x": 0.1, "y": 0.1, "width": 0.2, "height": 0.2}},
        severity=2,
    )
    set_spatial_keyframes(
        op,
        [
            SpatialKeyframe(10.0, {"x": 0.1, "y": 0.1, "width": 0.2, "height": 0.2}),
            SpatialKeyframe(14.0, {"x": 0.35, "y": 0.2, "width": 0.2, "height": 0.2}),
            SpatialKeyframe(20.0, {"x": 0.6, "y": 0.4, "width": 0.25, "height": 0.3}),
        ],
    )
    project = Project(operations=[op], selected_audio_stream_index=1)
    document = operations_to_corr_document(project)
    edits = document["edits"]
    assert len(edits) == 2
    assert edits[0]["end"] == edits[1]["start"]
    assert edits[0]["action"] == "cover"
    assert edits[0]["categories"] == ["other"]
    assert edits[1]["categories"] == ["other"]
    assert edits[0]["severity"] == 2
    assert edits[1]["severity"] == 2
    assert "box" in edits[0] and "box_end" in edits[0]
    assert "box_keyframes" not in edits[0]
    loaded = operations_from_corr_document(document, default_stream_index=1)
    assert len(loaded) == 1
    kfs = spatial_keyframes(loaded[0])
    assert loaded[0].severity == 2
    assert len(kfs) == 3
    assert abs(kfs[1].seconds - 14.0) < 1e-3
    assert abs(kfs[-1].box["x"] - 0.6) < 1e-3


def test_overflow_cover_round_trips() -> None:
    op = Operation.create(
        OperationType.COVER,
        -1,
        [],
        TimePoint.from_seconds_float(1.0, 48000),
        TimePoint.from_seconds_float(3.0, 48000),
        params={"box": {"x": -0.15, "y": 0.2, "width": 0.4, "height": 0.3}},
    )
    project = Project(operations=[op], selected_audio_stream_index=1)
    document = operations_to_corr_document(project)
    box = document["edits"][0]["box"]
    assert abs(box["x"] + 0.15) < 1e-9
    assert abs(box["width"] - 0.4) < 1e-9
    loaded = operations_from_corr_document(document, default_stream_index=1)
    assert abs(loaded[0].params["box"]["x"] + 0.15) < 1e-9
    assert abs(loaded[0].params["box"]["width"] - 0.4) < 1e-9


def test_corr_emits_video_matte_and_preserve_letterbox_on_zoom() -> None:
    zoom = Operation.create(
        OperationType.ZOOM,
        ITEM_LEVEL_STREAM_INDEX,
        [],
        TimePoint.from_seconds_float(1.0, 48000),
        TimePoint.from_seconds_float(2.0, 48000),
        params={"scale": 2.0, "x": 0.5, "y": 0.5},
    )
    project = Project(
        operations=[zoom],
        preserve_letterbox_on_zoom=True,
        video_matte=VideoMatteCache(x=0.0, y=0.1, width=1.0, height=0.8),
        selected_audio_stream_index=1,
    )
    document = operations_to_corr_document(project)
    assert document["video_matte"] == {
        "x": 0.0,
        "y": 0.1,
        "width": 1.0,
        "height": 0.8,
    }
    zoom_edit = next(e for e in document["edits"] if e["action"] == "zoom")
    assert zoom_edit["preserve_letterbox"] is True


def test_corr_omits_matte_when_preserve_disabled() -> None:
    zoom = Operation.create(
        OperationType.ZOOM,
        ITEM_LEVEL_STREAM_INDEX,
        [],
        TimePoint.from_seconds_float(1.0, 48000),
        TimePoint.from_seconds_float(2.0, 48000),
        params={"scale": 2.0, "x": 0.5, "y": 0.5},
    )
    project = Project(
        operations=[zoom],
        preserve_letterbox_on_zoom=False,
        video_matte=VideoMatteCache(x=0.0, y=0.1, width=1.0, height=0.8),
        selected_audio_stream_index=1,
    )
    document = operations_to_corr_document(project)
    assert "video_matte" not in document
    zoom_edit = next(e for e in document["edits"] if e["action"] == "zoom")
    assert "preserve_letterbox" not in zoom_edit


def test_corr_omits_trivial_video_matte() -> None:
    zoom = Operation.create(
        OperationType.ZOOM,
        ITEM_LEVEL_STREAM_INDEX,
        [],
        TimePoint.from_seconds_float(1.0, 48000),
        TimePoint.from_seconds_float(2.0, 48000),
        params={"scale": 2.0, "x": 0.5, "y": 0.5},
    )
    project = Project(
        operations=[zoom],
        preserve_letterbox_on_zoom=True,
        video_matte=VideoMatteCache(x=0.0, y=0.0, width=1.0, height=1.0),
        selected_audio_stream_index=1,
    )
    document = operations_to_corr_document(project)
    assert "video_matte" not in document
    zoom_edit = next(e for e in document["edits"] if e["action"] == "zoom")
    assert zoom_edit["preserve_letterbox"] is True


def test_save_corr_file_keeps_previous_file_when_replace_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "Title.corr.json"
    original = '{"schema_version": "1.0", "edits": []}\n'
    path.write_text(original, encoding="utf-8")

    def _fail_replace(*_args: object, **_kwargs: object) -> None:
        raise OSError("disk full")

    monkeypatch.setattr("corrcut.io.atomic_write.os.replace", _fail_replace)
    with pytest.raises(OSError, match="disk full"):
        save_corr_file(path, {"schema_version": "1.0", "edits": [{"action": "mute"}]})

    assert path.read_text(encoding="utf-8") == original
    assert list(tmp_path.glob("*.tmp")) == []
