"""Older project files may still contain export records. The app does not write movies."""

from __future__ import annotations

import corrcut.domain.export as export
from corrcut.domain.export import ExportRecord


def test_movie_file_export_presets_are_gone() -> None:
    assert not hasattr(export, "ExportPreset")
    assert not hasattr(export, "ExportOptions")
    assert not hasattr(export, "default_export_path")
    assert not hasattr(export, "manifest_now")


def test_historical_export_record_still_round_trips() -> None:
    raw = {
        "output_path": "C:/movies/Title_clean.mkv",
        "manifest": {
            "source_path": "C:/movies/Title.mkv",
            "output_path": "C:/movies/Title_clean.mkv",
            "edited_stream_index": 1,
            "preset": "mkv_ac3",
            "clean_track_codec": "ac3",
            "is_transcode": True,
            "filter_complex": "anull",
            "clean_track_name": "Edited",
            "clean_track_language": "eng",
            "set_clean_as_default": True,
            "completed_at": "2024-01-01T00:00:00+00:00",
        },
    }
    record = ExportRecord.from_json(raw)
    assert record.output_path.name == "Title_clean.mkv"
    assert record.manifest.preset == "mkv_ac3"
    assert record.to_json()["manifest"] == raw["manifest"]
