"""Subtitle cues are painted, not one widget per line."""

from __future__ import annotations

from fractions import Fraction
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QStyleOptionViewItem, QTableWidget, QToolButton

from corrcut.domain.project import Project, SourceFileRecord
from corrcut.domain.subtitle_cue import SubtitleCue, SubtitleSource
from corrcut.domain.time_point import TimePoint
from corrcut.ui.subtitle_cue_list import action_rects
from corrcut.ui.subtitles_panel import SubtitlesPanel

_app = QApplication.instance() or QApplication([])


def _cue(text: str, *, index: int, source: SubtitleSource = SubtitleSource.EMBEDDED) -> SubtitleCue:
    return SubtitleCue.create(
        start=TimePoint(Fraction(index)),
        end=TimePoint(Fraction(index + 1)),
        text=text,
        source=source,
        cue_id=f"cue-{index}",
    )


def _project(cues: list[SubtitleCue]) -> Project:
    project = Project(
        source=SourceFileRecord(
            path=Path("C:/media/test.mkv"),
            fingerprint={"size": 1},
            probe={"duration_seconds": float(len(cues) + 1)},
        ),
    )
    project.subtitle_cues = cues
    return project


def test_loading_many_cues_does_not_create_row_widgets(qtbot) -> None:
    panel = SubtitlesPanel()
    qtbot.addWidget(panel)
    cues = [_cue(f"line {index}", index=index) for index in range(2000)]
    buttons_before = len(panel.findChildren(QToolButton))
    seeks: list[float] = []
    panel.cue_seek.connect(seeks.append)

    panel.load_project(_project(cues))

    assert panel.findChildren(QTableWidget) == []
    assert len(panel.findChildren(QToolButton)) == buttons_before
    assert panel._cue_list.row_count() == 2000
    assert panel._cue_list.row_for_id("cue-1999") == 1999
    assert seeks == []
    assert panel.selected_cue() is not None
    assert panel.selected_cue().id == "cue-0"


def test_search_filters_by_rebuilding_visible_rows(qtbot) -> None:
    panel = SubtitlesPanel()
    qtbot.addWidget(panel)
    panel.load_project(
        _project(
            [
                _cue("alpha", index=0),
                _cue("beta needle", index=1),
                _cue("gamma", index=2),
            ]
        )
    )

    panel._cue_search.setText("needle")

    assert panel._cue_list.row_count() == 1
    assert panel._cue_list.cue_at(0) is not None
    assert panel._cue_list.cue_at(0).id == "cue-1"
    assert panel._cue_list.row_for_id("cue-0") == -1


def test_text_click_on_selected_row_seeks_and_delete_does_not(qtbot) -> None:
    panel = SubtitlesPanel()
    qtbot.addWidget(panel)
    cue = _cue("hello", index=0)
    panel.load_project(_project([cue, _cue("next", index=1)]))
    panel.resize(420, 320)
    panel.show()
    qtbot.waitExposed(panel)
    panel.select_cue(cue.id, scroll=False)

    cue_list = panel._cue_list
    rect = cue_list.visualRect(cue_list.model().index(0))
    seeks: list[float] = []
    deleted: list[str] = []
    panel.cue_seek.connect(seeks.append)
    panel.delete_cue_requested.connect(deleted.append)

    qtbot.mouseClick(cue_list.viewport(), Qt.MouseButton.LeftButton, pos=rect.center())
    assert seeks == [0.0]
    assert deleted == []

    _reset, delete = action_rects(rect)
    qtbot.mouseClick(cue_list.viewport(), Qt.MouseButton.LeftButton, pos=delete.center())
    assert deleted == [cue.id]
    assert seeks == [0.0]


def test_editing_displayed_text_emits_cue_edited(qtbot) -> None:
    panel = SubtitlesPanel()
    qtbot.addWidget(panel)
    cue = _cue("hello\nthere", index=0)
    panel.load_project(_project([cue]))
    edited: list[tuple[str, str]] = []
    panel.cue_edited.connect(lambda cue_id, text: edited.append((cue_id, text)))

    cue_list = panel._cue_list
    index = cue_list.model().index(0)
    delegate = cue_list.itemDelegate()
    editor = delegate.createEditor(cue_list, QStyleOptionViewItem(), index)
    editor.setText("hello\\nfriend")
    delegate.setModelData(editor, cue_list.model(), index)

    assert edited == [("cue-0", "hello\nfriend")]


def test_user_cue_reset_click_is_ignored(qtbot) -> None:
    panel = SubtitlesPanel()
    qtbot.addWidget(panel)
    cue = _cue("mine", index=0, source=SubtitleSource.USER)
    panel.load_project(_project([cue]))
    panel.resize(420, 320)
    panel.show()
    qtbot.waitExposed(panel)

    resets: list[str] = []
    panel.cue_reset.connect(resets.append)
    cue_list = panel._cue_list
    rect = cue_list.visualRect(cue_list.model().index(0))
    reset, _delete = action_rects(rect)
    qtbot.mouseClick(cue_list.viewport(), Qt.MouseButton.LeftButton, pos=reset.center())

    assert resets == []
