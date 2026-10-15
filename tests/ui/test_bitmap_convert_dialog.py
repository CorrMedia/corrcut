"""Bitmap OCR confirmation chooses a track only when several exist."""

from __future__ import annotations

from fractions import Fraction
from pathlib import Path

from PySide6.QtWidgets import QApplication, QComboBox, QDialog, QLabel, QMessageBox

from corrcut.domain.project import Project, SourceFileRecord
from corrcut.domain.subtitle_cue import SubtitleCue, SubtitleSource
from corrcut.domain.subtitle_settings import SubtitleSettings
from corrcut.domain.time_point import TimePoint
from corrcut.engine.subtitles.cue_sets import install_cue_set_for_tests
from corrcut.ui.bitmap_convert_dialog import BitmapConvertDialog
from corrcut.ui.subtitles_panel import SubtitlesPanel

_app = QApplication.instance() or QApplication([])


def _dialog(
    tracks: list[tuple[str, int]],
    *,
    preferred_index: int | None = None,
    replace_existing: bool = False,
) -> BitmapConvertDialog:
    return BitmapConvertDialog(
        None,
        tracks=tracks,
        preferred_index=preferred_index,
        replace_existing=replace_existing,
        intro="Convert a bitmap subtitle track?",
    )


def _label_text(dialog: BitmapConvertDialog) -> str:
    return " ".join(label.text() for label in dialog.findChildren(QLabel))


def test_single_track_is_named_without_a_combo() -> None:
    dialog = _dialog([("#2 hdmv_pgs_subtitle (eng)", 2)])

    assert dialog.findChildren(QComboBox) == []
    assert "Track: #2 hdmv_pgs_subtitle (eng)" in _label_text(dialog)
    assert "replaces the existing Bitmap OCR cues" not in _label_text(dialog)
    assert dialog.selected_stream_index() == 2


def test_several_tracks_default_to_auto_or_the_last_choice() -> None:
    tracks = [("#2 hdmv_pgs_subtitle (eng)", 2), ("#4 dvd_subtitle (fra)", 4)]
    dialog = _dialog(tracks, preferred_index=4, replace_existing=True)
    combo = dialog.findChild(QComboBox)

    assert combo is not None
    assert combo.itemText(0).startswith("Auto")
    assert combo.currentData() == 4
    assert "replaces the existing Bitmap OCR cues" in _label_text(dialog)
    assert dialog.selected_stream_index() == 4

    combo.setCurrentIndex(0)
    assert dialog.selected_stream_index() is None


def test_unknown_preferred_track_falls_back_to_auto() -> None:
    dialog = _dialog(
        [("#2 hdmv_pgs_subtitle (eng)", 2), ("#4 dvd_subtitle (fra)", 4)],
        preferred_index=9,
    )
    combo = dialog.findChild(QComboBox)

    assert combo is not None
    assert combo.currentIndex() == 0
    assert dialog.selected_stream_index() is None


def _project(*streams: dict[str, object], bitmap_stream_index: int | None = None) -> Project:
    return Project(
        source=SourceFileRecord(
            path=Path("C:/media/test.mkv"),
            fingerprint={"size": 1},
            probe={"duration_seconds": 10.0, "subtitle_streams": list(streams)},
        ),
        subtitle_settings=SubtitleSettings(bitmap_stream_index=bitmap_stream_index),
    )


def test_panel_keeps_bitmap_choice_off_the_main_layout() -> None:
    panel = SubtitlesPanel()
    project = _project(
        {"index": 2, "codec": "hdmv_pgs_subtitle", "language": "eng", "title": "English"},
        {"index": 4, "codec": "dvd_subtitle", "language": "fra"},
        {"index": 5, "codec": "dvb_subtitle", "language": "deu"},
        bitmap_stream_index=4,
    )
    panel.load_project(project)

    labels = " ".join(label.text() for label in panel.findChildren(QLabel))
    assert "Bitmap source" not in labels
    assert panel.subtitle_settings().bitmap_stream_index == 4
    assert panel.bitmap_convert_tracks() == [
        ("#2 hdmv_pgs_subtitle (eng) — English", 2),
        ("#4 dvd_subtitle (fra)", 4),
    ]

    panel.remember_bitmap_stream_index(2)
    assert panel.subtitle_settings().bitmap_stream_index == 2
    assert project.subtitle_settings.bitmap_stream_index == 2


def test_prompt_returns_the_dialog_choice(monkeypatch) -> None:
    panel = SubtitlesPanel()
    project = _project(
        {"index": 2, "codec": "hdmv_pgs_subtitle", "language": "eng"},
        {"index": 4, "codec": "dvd_subtitle", "language": "fra"},
    )
    install_cue_set_for_tests(
        project,
        [
            SubtitleCue.create(
                start=TimePoint(Fraction(0)),
                end=TimePoint(Fraction(1)),
                text="hello",
                source=SubtitleSource.OCR,
            )
        ],
        source=SubtitleSource.OCR,
    )
    panel.load_project(project)
    captured: dict[str, object] = {}

    class _Dialog:
        def __init__(self, *_args: object, **kwargs: object) -> None:
            captured.update(kwargs)

        def exec(self) -> QDialog.DialogCode:
            return QDialog.DialogCode.Accepted

        def selected_stream_index(self) -> int:
            return 4

    monkeypatch.setattr(
        "corrcut.ui.subtitles_panel.BitmapConvertDialog",
        _Dialog,
    )
    request = panel.prompt_bitmap_conversion(intro="Convert?")

    assert request is not None
    assert request.stream_index == 4
    assert captured["replace_existing"] is True
    assert captured["preferred_index"] is None
    assert captured["tracks"] == [
        ("#2 hdmv_pgs_subtitle (eng)", 2),
        ("#4 dvd_subtitle (fra)", 4),
    ]


def test_prompt_without_ocr_tracks_cancels(monkeypatch) -> None:
    panel = SubtitlesPanel()
    panel.load_project(_project({"index": 5, "codec": "dvb_subtitle", "language": "deu"}))
    monkeypatch.setattr(QMessageBox, "information", lambda *_args, **_kwargs: None)

    assert panel.prompt_bitmap_conversion(intro="Convert?") is None
