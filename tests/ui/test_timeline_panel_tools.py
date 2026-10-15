"""Timeline panel tool-strip applies create mode immediately."""

import pytest
from PySide6.QtCore import QPoint, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QToolButton

from corrcut.domain.operation import OperationType
from corrcut.ui.timeline.region_item import region_color
from corrcut.ui.timeline.timecode import format_timecode
from corrcut.ui.timeline.timeline_mode import TOOL_STRIP_ITEMS, TimelineMode
from corrcut.ui.timeline.timeline_panel import TimelinePanel

_app = QApplication.instance() or QApplication([])


def test_panel_stops_at_fully_open_tracks() -> None:
    from corrcut.ui.timeline.timeline_lanes import MAX_LANE_HEIGHT

    panel = TimelinePanel()
    panel.show()
    _app.processEvents()
    assert panel.maximumHeight() > panel.timeline.maximumHeight()
    panel.resize(640, panel.maximumHeight() + 400)
    _app.processEvents()
    assert panel.height() == panel.maximumHeight()
    assert panel.timeline.height() == panel.timeline.maximumHeight()
    assert panel.timeline._video_bg.rect().height() == pytest.approx(MAX_LANE_HEIGHT)  # noqa: SLF001


def test_blur_tool_button_sets_video_draw_type() -> None:
    panel = TimelinePanel()
    panel.show()
    _app.processEvents()

    panel.timeline.set_draw_operation_type(OperationType.COVER)
    blur_index = next(
        i for i, item in enumerate(TOOL_STRIP_ITEMS) if item.mode is TimelineMode.CREATE_BLUR
    )
    panel._on_tool_id_clicked(blur_index)  # noqa: SLF001
    _app.processEvents()

    assert panel.timeline.timeline_mode() is TimelineMode.SELECT
    assert panel.timeline._video_draw_type is OperationType.BLUR  # noqa: SLF001
    assert panel.timeline._draw_operation_type is OperationType.BLUR  # noqa: SLF001


def test_snap_toggle_controls_magnetic_snap() -> None:
    panel = TimelinePanel()
    panel.show()
    _app.processEvents()
    assert panel._snap_btn.isChecked()
    assert panel._snap_btn.text() == ""
    assert not panel._snap_btn.icon().isNull()
    assert "transparent" in panel._snap_btn.styleSheet()
    assert panel.timeline.magnetic_snap_enabled()
    panel._snap_btn.click()
    _app.processEvents()
    assert not panel._snap_btn.isChecked()
    assert not panel.timeline.magnetic_snap_enabled()
    panel._snap_btn.click()
    _app.processEvents()
    assert panel.timeline.magnetic_snap_enabled()


def test_scene_cuts_toggle_controls_markers_and_snap() -> None:
    panel = TimelinePanel()
    panel.show()
    _app.processEvents()
    assert panel._scene_cuts_btn.isChecked()
    assert panel._scene_cuts_btn.text() == ""
    assert not panel._scene_cuts_btn.icon().isNull()
    assert "transparent" in panel._scene_cuts_btn.styleSheet()
    assert panel.timeline.snap_to_scene_cuts_enabled()
    panel.timeline.set_scene_cut_times((8.0,))
    assert panel.timeline._ruler._scene_cut_times == (8.0,)  # noqa: SLF001

    panel._scene_cuts_btn.click()
    _app.processEvents()
    assert not panel._scene_cuts_btn.isChecked()
    assert not panel.timeline.snap_to_scene_cuts_enabled()
    assert panel.timeline._ruler._scene_cut_times == ()  # noqa: SLF001

    panel._scene_cuts_btn.click()
    _app.processEvents()
    assert panel.timeline.snap_to_scene_cuts_enabled()
    assert panel.timeline._ruler._scene_cut_times == (8.0,)  # noqa: SLF001


def test_timecode_and_zoom_are_off_the_tool_row() -> None:
    panel = TimelinePanel()
    panel.show()
    _app.processEvents()

    assert panel._tool_buttons[TimelineMode.CREATE_VOLUME].text() == "Volume"
    assert panel._tool_buttons[TimelineMode.CREATE_PIXELATE].text() == "Pixelate"
    assert panel._zoom_slider.parent() is panel._chrome_bar
    assert panel._zoom_icon.parent() is panel._chrome_bar
    assert panel._timecode_edit.parent() is panel._chrome_bar
    assert panel._timecode_edit.hasFrame() is False
    assert not hasattr(panel, "_duration_label")
    mute = panel._tool_buttons[TimelineMode.CREATE_MUTE]
    assert mute.parent() is panel._tools_bar
    assert panel._snap_btn.parent() is panel._tools_bar
    assert panel.tool_status_text() == "Tool: Select"


def test_tool_label_tracks_the_checked_chip() -> None:
    panel = TimelinePanel()
    seen: list[str] = []
    panel.tool_label_changed.connect(seen.append)

    panel.set_tool_mode(TimelineMode.CREATE_MUTE)
    assert seen == ["Tool: Mute"]
    assert panel.tool_status_text() == "Tool: Mute"

    panel._tool_buttons[TimelineMode.CREATE_BLUR].click()
    _app.processEvents()
    assert seen[-1] == "Tool: Blur"
    assert panel._tool_buttons[TimelineMode.CREATE_BLUR].isChecked()


def test_tool_status_stays_while_messages_flash(editor_window) -> None:  # type: ignore[no-untyped-def]
    label = editor_window._tool_status
    duration = editor_window._duration_label
    assert label.text() == "Tool: Select"
    assert label.parent() is editor_window.statusBar()
    assert duration.parent() is editor_window.statusBar()
    timeline = editor_window._timeline
    assert duration.text() == "Length: 00:00:00.000"
    assert timeline._content_width() == pytest.approx(timeline.viewport().width(), abs=1.0)  # noqa: SLF001
    assert timeline.horizontalScrollBar().maximum() == 0
    panel = editor_window._timeline_panel
    mute = panel._tool_buttons[TimelineMode.CREATE_MUTE]  # noqa: SLF001
    assert not mute.isEnabled()
    assert not panel._zoom_slider.isEnabled()  # noqa: SLF001
    assert not panel._fit_btn.isEnabled()  # noqa: SLF001
    assert not editor_window._timeline.edits_enabled()
    assert not editor_window._timeline._navigator._interactive  # noqa: SLF001
    assert not editor_window._timeline_tool_actions[0].isEnabled()
    assert duration.toolTip() == "Clip duration"

    editor_window._timeline_panel.set_tool_mode(TimelineMode.CREATE_BLUR)
    _app.processEvents()
    assert label.text() == "Tool: Blur"

    editor_window.statusBar().showMessage("Waveform job running…", 2000)
    assert label.text() == "Tool: Blur"
    assert editor_window.statusBar().currentMessage() == "Waveform job running…"


def test_playhead_time_is_plain_until_edited() -> None:
    panel = TimelinePanel()
    panel.show()
    panel.timeline.set_duration(120.0)
    panel.sync_playhead_display(65.5)
    _app.processEvents()

    edit = panel._timecode_edit  # noqa: SLF001
    css = edit.styleSheet().lower()
    assert "background: transparent" in css
    assert edit.text() == "00:01:05.500"
    assert edit.fontMetrics().height() > panel.fontMetrics().height()
    assert edit.fontMetrics().horizontalAdvance(edit.text()) <= edit.width()
    assert edit.isReadOnly()
    assert edit.cursor().shape() == Qt.CursorShape.ArrowCursor
    assert edit.toolTip() == "Playhead — double-click to type a time, Enter to seek"

    QTest.mouseClick(edit, Qt.MouseButton.LeftButton)
    _app.processEvents()
    assert edit.isReadOnly()
    assert not edit.hasFocus()
    assert edit.selectedText() == ""

    QTest.mouseDClick(edit, Qt.MouseButton.LeftButton)
    _app.processEvents()
    assert not edit.isReadOnly()
    assert edit.hasFocus()
    assert edit.cursor().shape() == Qt.CursorShape.IBeamCursor
    assert edit.selectedText() == edit.text()

    edit.setText("00:00:10")
    panel.sync_playhead_display(3.0)
    assert edit.text() == "00:00:10"
    assert panel.timeline.playhead_seconds() == 3.0

    QTest.keyClick(edit, Qt.Key.Key_Return)
    _app.processEvents()
    assert panel.timeline.playhead_seconds() == 10.0
    assert not edit.hasFocus()
    assert edit.isReadOnly()
    assert edit.text() == format_timecode(10.0)
    assert edit.cursor().shape() == Qt.CursorShape.ArrowCursor

    QTest.mouseDClick(edit, Qt.MouseButton.LeftButton)
    _app.processEvents()
    assert edit.selectedText() == edit.text()
    edit.setText("not-a-time")
    QTest.keyClick(edit, Qt.Key.Key_Escape)
    _app.processEvents()
    assert not edit.hasFocus()
    assert edit.isReadOnly()
    assert edit.text() == format_timecode(10.0)
    assert panel.timeline.playhead_seconds() == 10.0

    panel.resize(720, 280)
    _app.processEvents()
    bar = panel._chrome_bar  # noqa: SLF001
    outside = QPoint(edit.width() + 36, max(2, bar.height() // 2))
    assert bar.width() > outside.x()

    QTest.mouseDClick(edit, Qt.MouseButton.LeftButton)
    _app.processEvents()
    QTest.mouseClick(edit, Qt.MouseButton.LeftButton)
    _app.processEvents()
    assert edit.hasFocus()
    assert not edit.isReadOnly()

    QTest.mouseClick(bar, Qt.MouseButton.LeftButton, pos=outside)
    _app.processEvents()
    assert panel.timeline.playhead_seconds() == 10.0
    assert not edit.hasFocus()
    assert edit.isReadOnly()
    assert edit.text() == format_timecode(10.0)

    QTest.mouseDClick(edit, Qt.MouseButton.LeftButton)
    _app.processEvents()
    edit.setText("12.5")
    QTest.mouseClick(bar, Qt.MouseButton.LeftButton, pos=outside)
    _app.processEvents()
    assert panel.timeline.playhead_seconds() == 12.5
    assert not edit.hasFocus()
    assert edit.isReadOnly()
    assert edit.text() == format_timecode(12.5)


def test_tool_strip_can_be_disabled() -> None:
    panel = TimelinePanel()
    panel.set_tools_enabled(False)
    mute = panel._tool_buttons[TimelineMode.CREATE_MUTE]  # noqa: SLF001
    assert not mute.isEnabled()
    assert not panel._snap_btn.isEnabled()  # noqa: SLF001
    assert not panel._zoom_slider.isEnabled()  # noqa: SLF001
    assert not panel._fit_btn.isEnabled()  # noqa: SLF001
    assert not panel._timecode_edit._editing_enabled  # noqa: SLF001
    panel._on_tool_id_clicked(1)  # noqa: SLF001
    assert panel.tool_status_text() == "Tool: Select"
    panel.set_tools_enabled(True)
    assert mute.isEnabled()
    assert panel.tools_enabled()


def test_zoom_slider_is_compact_neutral_chrome() -> None:
    panel = TimelinePanel()
    panel.show()
    _app.processEvents()

    slider = panel._zoom_slider  # noqa: SLF001
    assert slider.maximumWidth() == 200
    assert slider.width() <= 200
    css = slider.styleSheet().lower()
    blur = region_color(OperationType.BLUR)
    assert blur.name().lower() not in css
    assert panel._zoom_icon.parent() is panel._chrome_bar  # noqa: SLF001
    assert not panel._zoom_icon.pixmap().isNull()  # noqa: SLF001
    assert panel._zoom_icon.toolTip() == "Timeline zoom"  # noqa: SLF001
    plus_minus = [
        btn
        for btn in panel.findChildren(QToolButton)
        if btn.text() in ("+", "−", "-", "–")
    ]
    assert plus_minus == []
