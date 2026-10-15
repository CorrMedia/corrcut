"""Operations list and living-room channel picker."""

from fractions import Fraction

import pytest
from PySide6.QtCore import QPoint, Qt
from PySide6.QtGui import QFont, QFontMetrics

from corrcut.domain.dialogue_target import DialogueTargetStrategy
from corrcut.domain.operation import Operation, OperationType
from corrcut.domain.time_point import TimePoint
from corrcut.ui.operations_list import (
    ROW_HEIGHT,
    accent_color,
    category_subtitle_for,
    format_span,
    subtitle_for,
    subtitle_slot_widths,
)
from corrcut.ui.operations_panel import OperationsPanel


def _mute_op(*, channels: list[str]) -> Operation:
    return Operation.create(
        OperationType.MUTE,
        0,
        channels,
        TimePoint(Fraction(1)),
        TimePoint(Fraction(2)),
    )


def _visual_op(op_type: OperationType = OperationType.BLUR) -> Operation:
    return Operation.create(
        op_type,
        -1,
        [],
        TimePoint(Fraction(1)),
        TimePoint(Fraction(2)),
    )


def _row_visible(panel: OperationsPanel, field) -> bool:
    row = panel._form.getWidgetPosition(field)[0]
    return panel._form.isRowVisible(row)


def _load_51(panel: OperationsPanel, operations: list[Operation], **kwargs) -> None:
    panel.load_operations(
        operations,
        strategy=DialogueTargetStrategy.CENTER_CHANNEL,
        channel_layout="5.1",
        channels=6,
        **kwargs,
    )


def test_list_has_no_channel_columns(qtbot) -> None:
    panel = OperationsPanel()
    qtbot.addWidget(panel)
    _load_51(panel, [_mute_op(channels=["FC"])])
    assert panel._list.list_model.rowCount() == 1
    assert panel._type_combo.findData(OperationType.MUTE.value) >= 0


def test_edits_count_stays_beside_title_not_over_inspector(qtbot) -> None:
    panel = OperationsPanel()
    qtbot.addWidget(panel)
    first = _mute_op(channels=["FC"])
    second = _mute_op(channels=["FC"])
    _load_51(panel, [first, second])
    panel.resize(720, 400)
    panel.show()
    qtbot.waitExposed(panel)
    assert panel._count_label.text() == "(2)"
    count_x = panel._count_label.mapTo(panel, QPoint(0, 0)).x()
    inspector_x = panel._inspector.mapTo(panel, QPoint(0, 0)).x()
    assert count_x < inspector_x
    assert count_x < 160


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("FC", True),
        ("FL", True),
        ("FR", True),
        ("BL", False),
        ("BR", False),
    ],
)
def test_editor_channel_checks_for_center_strategy(qtbot, name, expected) -> None:
    panel = OperationsPanel()
    qtbot.addWidget(panel)
    op = _mute_op(channels=["FC", "FL", "FR"])
    _load_51(panel, [op])
    panel.update_operation_editor(op)
    assert panel._channel_names == ["FC", "FL", "FR", "BL", "BR"]
    assert _row_visible(panel, panel._channels_block)
    assert panel._channel_checks[name].isChecked() is expected


def test_center_only_mute_can_be_toggled_off(qtbot) -> None:
    panel = OperationsPanel()
    qtbot.addWidget(panel)
    op = _mute_op(channels=["FC"])
    _load_51(panel, [op])
    panel.resize(640, 400)
    panel.show()
    qtbot.waitExposed(panel)
    panel.update_operation_editor(op)
    assert panel._channel_checks["FC"].isChecked()
    with qtbot.waitSignal(panel.channels_changed, timeout=1000) as blocker:
        qtbot.mouseClick(
            panel._channels_widget,
            Qt.MouseButton.LeftButton,
            pos=panel._channels_widget.speaker_rect("FC").center(),
        )
    assert blocker.args == [op.id, []]
    assert not panel._channel_checks["FC"].isChecked()


def test_center_speaker_can_be_unchecked(qtbot) -> None:
    panel = OperationsPanel()
    qtbot.addWidget(panel)
    op = _mute_op(channels=["FC", "FL"])
    _load_51(panel, [op])
    panel.resize(640, 400)
    panel.show()
    qtbot.waitExposed(panel)
    panel.update_operation_editor(op)
    with qtbot.waitSignal(panel.channels_changed, timeout=1000) as blocker:
        qtbot.mouseClick(
            panel._channels_widget,
            Qt.MouseButton.LeftButton,
            pos=panel._channels_widget.speaker_rect("FC").center(),
        )
    assert blocker.args[0] == op.id
    assert "FC" not in blocker.args[1]
    assert "FL" in blocker.args[1]


def test_channel_room_click_emits_channels_changed(qtbot) -> None:
    panel = OperationsPanel()
    qtbot.addWidget(panel)
    op = _mute_op(channels=["FC"])
    _load_51(panel, [op])
    panel.resize(640, 400)
    panel.show()
    qtbot.waitExposed(panel)
    panel.update_operation_editor(op)
    with qtbot.waitSignal(panel.channels_changed, timeout=1000) as blocker:
        qtbot.mouseClick(
            panel._channels_widget,
            Qt.MouseButton.LeftButton,
            pos=panel._channels_widget.speaker_rect("BL").center(),
        )
    assert blocker.args == [op.id, ["FC", "BL"]]


def test_channel_toggle_emits_channels_changed(qtbot) -> None:
    panel = OperationsPanel()
    qtbot.addWidget(panel)
    op = _mute_op(channels=["FC"])
    _load_51(panel, [op])
    panel.update_operation_editor(op)
    with qtbot.waitSignal(panel.channels_changed, timeout=1000) as blocker:
        panel._channel_checks["BL"].setChecked(True)
    assert blocker.args == [op.id, ["FC", "BL"]]


def test_click_emits_selection_when_row_already_selected(qtbot) -> None:
    panel = OperationsPanel()
    qtbot.addWidget(panel)
    op = _mute_op(channels=["FC"])
    _load_51(panel, [op])
    panel.resize(360, 560)
    panel.show()
    qtbot.waitExposed(panel)
    panel.select_operation(op.id, emit_selection_changed=False)

    index = panel._list.list_model.index(0)
    rect = panel._list.visualRect(index)
    with qtbot.waitSignal(panel.selection_changed, timeout=1000) as blocker:
        qtbot.mouseClick(
            panel._list.viewport(),
            Qt.MouseButton.LeftButton,
            pos=rect.center(),
        )
    assert blocker.args == [op.id]


def test_full_mix_shows_channel_checks(qtbot) -> None:
    panel = OperationsPanel()
    qtbot.addWidget(panel)
    op = _mute_op(channels=["FL"])
    panel.load_operations(
        [op],
        strategy=DialogueTargetStrategy.FULL_MIX,
        channel_layout="stereo",
        channels=2,
    )
    panel.update_operation_editor(op)
    assert panel._channel_names == ["FL", "FR"]
    assert _row_visible(panel, panel._channels_block)
    assert panel._channel_checks["FL"].isChecked()
    assert not panel._channel_checks["FR"].isChecked()
    assert "click a speaker" in panel._channel_note.text().lower()
    assert "toggle all" in panel._channel_note.text().lower()
    assert panel._channel_note.x() == panel._channels_widget.x()


def test_toggle_all_link_selects_then_clears_speakers(qtbot) -> None:
    panel = OperationsPanel()
    qtbot.addWidget(panel)
    op = _mute_op(channels=["FC"])
    _load_51(panel, [op])
    panel.update_operation_editor(op)
    assert panel._channel_checks["FC"].isChecked()
    assert not panel._channel_checks["BL"].isChecked()
    with qtbot.waitSignal(panel.channels_changed, timeout=1000) as blocker:
        panel._channel_note.linkActivated.emit("toggle-all")
    assert blocker.args == [op.id, ["FC", "FL", "FR", "BL", "BR"]]
    assert all(box.isChecked() for box in panel._channel_checks.values())
    with qtbot.waitSignal(panel.channels_changed, timeout=1000) as blocker:
        panel._channel_note.linkActivated.emit("toggle-all")
    assert blocker.args == [op.id, []]
    assert not any(box.isChecked() for box in panel._channel_checks.values())


def test_audio_edit_shows_language_at_the_bottom(qtbot) -> None:
    panel = OperationsPanel()
    qtbot.addWidget(panel)
    op = Operation.create(
        OperationType.MUTE,
        0,
        ["FC"],
        TimePoint(Fraction(1)),
        TimePoint(Fraction(2)),
        language="spa",
    )
    _load_51(panel, [op], track_languages={0: ("spa", False)})
    panel.update_operation_editor(op)
    assert _row_visible(panel, panel._language_label)
    assert panel._language_label.text() == "Spanish"
    assert "font-style: italic" in panel._language_label.styleSheet()
    language_row = panel._form.getWidgetPosition(panel._language_label)[0]
    severity_row = panel._form.getWidgetPosition(panel._severity_combo)[0]
    assert language_row == severity_row + 1
    assert language_row == panel._form.rowCount() - 1


def test_assumed_english_stays_on_the_language_row(qtbot) -> None:
    panel = OperationsPanel()
    qtbot.addWidget(panel)
    op = _mute_op(channels=["FC"])
    _load_51(panel, [op], track_languages={0: ("eng", True)})
    panel.update_operation_editor(op)
    assert panel._language_label.text() == "English (assumed)"
    explicit = Operation.create(
        OperationType.BEEP,
        0,
        ["FC"],
        TimePoint(Fraction(1)),
        TimePoint(Fraction(2)),
        language="eng",
    )
    _load_51(panel, [explicit], track_languages={0: ("eng", False)})
    panel.update_operation_editor(explicit)
    assert panel._language_label.text() == "English"


def test_visual_edit_hides_language(qtbot) -> None:
    panel = OperationsPanel()
    qtbot.addWidget(panel)
    cover = _visual_op(OperationType.COVER)
    _load_51(panel, [cover], track_languages={0: ("eng", True)})
    panel.update_operation_editor(cover)
    assert not _row_visible(panel, panel._language_label)


def test_cover_hides_channel_checks_on_51(qtbot) -> None:
    panel = OperationsPanel()
    qtbot.addWidget(panel)
    cover = Operation.create(
        OperationType.COVER,
        -1,
        [],
        TimePoint(Fraction(1)),
        TimePoint(Fraction(2)),
    )
    mute = _mute_op(channels=["FC"])
    _load_51(panel, [mute, cover])
    panel.update_operation_editor(cover)
    assert not _row_visible(panel, panel._channels_block)
    panel.update_operation_editor(mute)
    assert _row_visible(panel, panel._channels_block)


def test_follow_playhead_selects_matching_operation_without_selection_signal(qtbot) -> None:
    panel = OperationsPanel()
    qtbot.addWidget(panel)
    panel.resize(400, 220)
    first = Operation.create(
        OperationType.MUTE,
        0,
        ["FC"],
        TimePoint(Fraction(1)),
        TimePoint(Fraction(2)),
    )
    second = Operation.create(
        OperationType.MUTE,
        0,
        ["FC"],
        TimePoint(Fraction(3)),
        TimePoint(Fraction(4)),
    )
    _load_51(panel, [first, second])
    panel._list.setFixedHeight(90)
    panel.show()
    qtbot.waitExposed(panel)

    with qtbot.assertNotEmitted(panel.selection_changed):
        panel.follow_playhead(3.5)
    assert panel._list.current_row() == 1
    assert panel._current_id == second.id


def test_follow_playhead_keeps_overlapping_selection(qtbot) -> None:
    panel = OperationsPanel()
    qtbot.addWidget(panel)
    first = Operation.create(
        OperationType.COVER,
        -1,
        [],
        TimePoint(Fraction(2)),
        TimePoint(Fraction(5)),
    )
    second = Operation.create(
        OperationType.BLUR,
        -1,
        [],
        TimePoint(Fraction(2)),
        TimePoint(Fraction(5)),
    )
    _load_51(panel, [first, second])

    panel.select_operation(second.id, emit_selection_changed=False)
    assert panel._current_id == second.id

    with qtbot.assertNotEmitted(panel.selection_changed):
        panel.follow_playhead(3.5)
    assert panel._current_id == second.id
    assert panel._list.current_row() == panel._row_for_op_id(second.id)


def test_follow_playhead_keeps_visible_row_in_place(qtbot) -> None:
    """A click seeks the playhead, which follows the same row. Do not center it."""
    panel = OperationsPanel()
    qtbot.addWidget(panel)
    operations = [
        Operation.create(
            OperationType.MUTE,
            0,
            ["FC"],
            TimePoint(Fraction(index)),
            TimePoint(Fraction(index + 1)),
        )
        for index in range(12)
    ]
    _load_51(panel, operations)
    # Five rows visible: row 3 sits in the lower half, fully on screen.
    panel._list.setFixedHeight(ROW_HEIGHT * 5)
    panel.show()
    qtbot.waitExposed(panel)
    panel._list.select_row(3, scroll=False)
    bar = panel._list.verticalScrollBar()
    assert bar.maximum() > 0
    before = bar.value()

    panel.follow_playhead(3.5)

    assert panel._list.current_row() == 3
    assert bar.value() == before


def _candidate_ids(panel: OperationsPanel, query: str = "") -> list[str]:
    return [option.id for option in panel._categories._candidates(query)]


def test_category_chips_load_from_operation(qtbot) -> None:
    panel = OperationsPanel()
    qtbot.addWidget(panel)

    op = Operation.create(
        OperationType.MUTE,
        0,
        ["FC"],
        TimePoint(Fraction(1)),
        TimePoint(Fraction(2)),
        categories=["word_ass"],
    )
    _load_51(panel, [op])
    panel.update_operation_editor(op)
    assert panel._categories.selected_ids() == ["word_ass"]


def test_audio_edit_search_offers_spoken_not_visual(qtbot) -> None:
    panel = OperationsPanel()
    qtbot.addWidget(panel)
    op = _mute_op(channels=["FC"])
    _load_51(panel, [op])
    panel.update_operation_editor(op)
    ids = _candidate_ids(panel)
    assert "word_fuck" in ids
    assert "sexual_reference" in ids
    assert "drugs_illegal" in ids
    assert "gore" not in ids
    assert "female_nudity" not in ids
    assert "captions_profanity" not in ids
    assert "opening_credits" not in ids
    # Default mute tags as other, so it is a chip rather than a candidate.
    assert panel._categories.selected_ids() == ["other"]
    assert "other" not in ids


def test_visual_edit_search_offers_visual_not_spoken(qtbot) -> None:
    panel = OperationsPanel()
    qtbot.addWidget(panel)
    op = _visual_op()
    _load_51(panel, [op])
    panel.update_operation_editor(op)
    ids = _candidate_ids(panel)
    assert "word_fuck" not in ids
    assert "sexual_reference" not in ids
    assert "gore" in ids
    assert "captions_profanity" in ids
    assert "opening_credits" in ids
    assert panel._categories.selected_ids() == ["other"]
    assert "other" not in ids


def test_skip_edit_search_shows_credits_not_profanity(qtbot) -> None:
    panel = OperationsPanel()
    qtbot.addWidget(panel)
    op = _visual_op(OperationType.SKIP)
    _load_51(panel, [op])
    panel.update_operation_editor(op)
    ids = _candidate_ids(panel)
    assert "word_fuck" not in ids
    assert "opening_credits" in ids
    assert "gore" in ids


def test_selected_audio_chip_survives_visual_type_change(qtbot) -> None:
    panel = OperationsPanel()
    qtbot.addWidget(panel)
    op = _mute_op(channels=["FC"])
    op.categories = ["word_ass"]
    _load_51(panel, [op])
    panel.update_operation_editor(op)
    with qtbot.waitSignal(panel.censor_params_changed, timeout=1000):
        panel._type_combo.setCurrentIndex(
            panel._type_combo.findData(OperationType.BLUR.value)
        )
    assert panel._categories.selected_ids() == ["word_ass"]
    ids = _candidate_ids(panel)
    assert "word_ass" not in ids  # already selected
    assert "word_fuck" not in ids
    assert "gore" in ids


def test_category_search_filters_candidates(qtbot) -> None:
    panel = OperationsPanel()
    qtbot.addWidget(panel)
    op = _mute_op(channels=["FC"])
    _load_51(panel, [op])
    panel.update_operation_editor(op)
    assert "word_fuck" in _candidate_ids(panel, "fuck")
    assert "gore" not in _candidate_ids(panel, "fuck")
    assert "gore" in _candidate_ids(panel, "violence")
    assert "word_fuck" not in _candidate_ids(panel, "violence")


def test_category_search_keeps_existing_chips(qtbot) -> None:
    panel = OperationsPanel()
    qtbot.addWidget(panel)
    op = _mute_op(channels=["FC"])
    op.categories = ["word_ass"]
    _load_51(panel, [op])
    panel.update_operation_editor(op)
    assert "word_ass" not in _candidate_ids(panel, "credits")
    assert panel._categories.selected_ids() == ["word_ass"]


def test_category_chips_allow_multiple(qtbot) -> None:
    panel = OperationsPanel()
    qtbot.addWidget(panel)
    op = _mute_op(channels=["FC"])
    op.categories = ["word_ass"]
    _load_51(panel, [op])
    panel.update_operation_editor(op)
    with qtbot.waitSignal(panel.operation_meta_changed, timeout=1000):
        panel._categories._add_category("word_fuck")
    assert "word_ass" in op.categories
    assert "word_fuck" in op.categories


def test_removing_chip_updates_operation(qtbot) -> None:
    panel = OperationsPanel()
    qtbot.addWidget(panel)
    op = _mute_op(channels=["FC"])
    op.categories = ["word_ass", "word_fuck"]
    _load_51(panel, [op])
    panel.update_operation_editor(op)
    with qtbot.waitSignal(panel.operation_meta_changed, timeout=1000):
        panel._categories._remove_category("word_fuck")
    assert op.categories == ["word_ass"]


def test_list_rows_use_timeline_type_accents() -> None:
    mute = accent_color(OperationType.MUTE)
    blur = accent_color(OperationType.BLUR)
    assert mute != blur
    assert mute.red() > mute.blue()
    assert blur.blue() > blur.red()


def test_format_span_is_compact_timecode() -> None:
    assert format_span(0) == "0:00.000"
    assert format_span(62.5) == "1:02.500"
    assert format_span(3661.0).startswith("1:01:01")


def test_subtitle_lists_description_before_categories() -> None:
    op = Operation.create(
        OperationType.MUTE,
        0,
        ["FC"],
        TimePoint(Fraction(1)),
        TimePoint(Fraction(2)),
        categories=["word_fuck"],
        description="said fuck",
    )
    categories = category_subtitle_for(op)
    assert categories
    assert "Profanity" in categories
    combined = subtitle_for(op)
    assert combined.startswith("said fuck")
    assert categories in combined
    assert combined.index("said fuck") < combined.index(categories)


def test_subtitle_without_description_is_categories() -> None:
    op = Operation.create(
        OperationType.MUTE,
        0,
        ["FC"],
        TimePoint(Fraction(1)),
        TimePoint(Fraction(2)),
        categories=["word_ass"],
    )
    assert subtitle_for(op) == category_subtitle_for(op)
    assert subtitle_for(op)


def test_subtitle_slots_elide_description_and_categories_separately(qtbot) -> None:
    metrics = QFontMetrics(QFont())
    description = "this is a very long description that should not hide categories"
    categories = "Profanity: f*ck, Language: Profanity, Violence: Gore"
    desc_w, cat_w = subtitle_slot_widths(metrics, description, categories, 140)
    assert desc_w > 0
    assert cat_w > 0
    desc = metrics.elidedText(description, Qt.TextElideMode.ElideRight, desc_w)
    cat = metrics.elidedText(categories, Qt.TextElideMode.ElideRight, cat_w)
    assert desc != description
    assert cat != categories
    desc_only, _ = subtitle_slot_widths(metrics, "", categories, 200)
    assert desc_only == 0
    _, cat_full = subtitle_slot_widths(metrics, "", categories, 200)
    assert cat_full == 200


def test_type_menu_groups_audio_and_visual(qtbot) -> None:
    panel = OperationsPanel()
    qtbot.addWidget(panel)
    combo = panel._type_combo
    labels = [combo.itemText(i) for i in range(combo.count())]
    audio_header = labels.index("Audio")
    visual_header = labels.index("Visual")
    mute = combo.findData(OperationType.MUTE.value)
    beep = combo.findData(OperationType.BEEP.value)
    cover = combo.findData(OperationType.COVER.value)
    skip = combo.findData(OperationType.SKIP.value)
    assert audio_header < mute < beep < visual_header < cover < skip
    assert not combo.model().item(audio_header).isEnabled()
    assert not combo.model().item(visual_header).isEnabled()
    assert combo.itemIcon(mute).isNull() is False
    assert combo.itemIcon(cover).isNull() is False
    assert combo.itemIcon(mute).cacheKey() != combo.itemIcon(cover).cacheKey()


def test_inspector_type_change_emits(qtbot) -> None:
    panel = OperationsPanel()
    qtbot.addWidget(panel)
    op = _mute_op(channels=["FC"])
    _load_51(panel, [op])
    panel.update_operation_editor(op)
    with qtbot.waitSignal(panel.censor_params_changed, timeout=1000) as blocker:
        panel._type_combo.setCurrentIndex(panel._type_combo.findData(OperationType.BLUR.value))
    assert blocker.args[0] == op.id
    assert blocker.args[1] == OperationType.BLUR


def test_enabled_toggle_switches_and_emits(qtbot) -> None:
    panel = OperationsPanel()
    qtbot.addWidget(panel)
    op = _mute_op(channels=["FC"])
    _load_51(panel, [op])
    panel.update_operation_editor(op)
    assert panel._enabled_check.isChecked()
    assert op.enabled
    with qtbot.waitSignal(panel.enabled_changed, timeout=1000) as blocker:
        panel._enabled_check.setChecked(False)
    assert blocker.args == [op.id, False]
    assert op.enabled is False
    assert panel._enabled_check.objectName() == "enabledToggle"


def test_d_shortcut_toggles_enabled_when_list_focused(qtbot) -> None:
    panel = OperationsPanel()
    qtbot.addWidget(panel)
    op = _mute_op(channels=["FC"])
    _load_51(panel, [op])
    panel.resize(640, 400)
    panel.show()
    qtbot.waitExposed(panel)
    panel.select_operation(op.id, emit_selection_changed=False)
    panel.update_operation_editor(op)
    panel._list.setFocus()
    with qtbot.waitSignal(panel.enabled_changed, timeout=1000) as blocker:
        qtbot.keyClick(panel._list, Qt.Key.Key_D)
    assert blocker.args == [op.id, False]
    assert op.enabled is False


def test_d_in_description_does_not_toggle_enabled(qtbot) -> None:
    panel = OperationsPanel()
    qtbot.addWidget(panel)
    op = _mute_op(channels=["FC"])
    _load_51(panel, [op])
    panel.resize(640, 400)
    panel.show()
    qtbot.waitExposed(panel)
    panel.update_operation_editor(op)
    panel._description_edit.setFocus()
    with qtbot.assertNotEmitted(panel.enabled_changed):
        qtbot.keyClick(panel._description_edit, Qt.Key.Key_D)
    assert op.enabled
    assert "d" in panel._description_edit.text().lower()


def test_severity_combo_round_trips(qtbot) -> None:
    panel = OperationsPanel()
    qtbot.addWidget(panel)
    op = _mute_op(channels=["FC"])
    op.severity = 2
    _load_51(panel, [op])
    panel.update_operation_editor(op)
    assert panel._severity_combo.currentData() == 2
    assert panel._severity_combo.currentText() == "Mild"
    with qtbot.waitSignal(panel.operation_meta_changed, timeout=1000) as blocker:
        panel._severity_combo.setCurrentIndex(panel._severity_combo.findData(4))
    assert blocker.args == [op.id]
    assert op.severity == 4
    assert panel._severity_combo.currentText() == "Severe"
    panel._severity_combo.setCurrentIndex(0)
    assert op.severity is None
    assert panel._severity_combo.currentText() == "(Optional)"


def test_severity_combo_keeps_unknown_number(qtbot) -> None:
    panel = OperationsPanel()
    qtbot.addWidget(panel)
    op = _mute_op(channels=["FC"])
    op.severity = 6
    _load_51(panel, [op])
    panel.update_operation_editor(op)
    assert panel._severity_combo.currentData() == 6
    assert panel._severity_combo.currentText() == "6"


def test_delete_key_emits_delete_requested(qtbot) -> None:
    panel = OperationsPanel()
    qtbot.addWidget(panel)
    op = _mute_op(channels=["FC"])
    _load_51(panel, [op])
    panel.resize(360, 560)
    panel.show()
    qtbot.waitExposed(panel)
    panel.select_operation(op.id, emit_selection_changed=False)
    panel._list.setFocus()
    with qtbot.waitSignal(panel.delete_requested, timeout=1000) as blocker:
        qtbot.keyClick(panel._list, Qt.Key.Key_Delete)
    assert blocker.args == [op.id]


def test_inspector_fields_flow_from_top(qtbot) -> None:
    panel = OperationsPanel()
    qtbot.addWidget(panel)
    op = _mute_op(channels=["FC"])
    _load_51(panel, [op])
    panel.resize(640, 520)
    panel.show()
    qtbot.waitExposed(panel)
    panel.update_operation_editor(op)
    qtbot.wait(10)

    enabled_y = panel._enabled_check.mapTo(
        panel._editor, panel._enabled_check.rect().topLeft()
    ).y()
    assert enabled_y <= 24
    type_y = panel._type_combo.mapTo(panel._editor, panel._type_combo.rect().topLeft()).y()
    assert type_y > enabled_y
    channels_y = panel._channels_block.mapTo(
        panel._editor, panel._channels_block.rect().topLeft()
    ).y()
    assert channels_y > type_y
    cat_y = panel._categories.mapTo(
        panel._editor, panel._categories.rect().topLeft()
    ).y()
    assert cat_y > channels_y
    desc_y = panel._description_edit.mapTo(
        panel._editor, panel._description_edit.rect().topLeft()
    ).y()
    assert desc_y > cat_y
    assert panel._categories.search_field.isVisible()


def test_channel_checks_stay_with_label(qtbot) -> None:
    panel = OperationsPanel()
    qtbot.addWidget(panel)
    op = _mute_op(channels=["FC"])
    _load_51(panel, [op])
    panel.resize(640, 400)
    panel.show()
    qtbot.waitExposed(panel)
    panel.update_operation_editor(op)
    qtbot.wait(10)

    label = panel._form.labelForField(panel._channels_block)
    assert label is not None
    assert 80 <= panel._channels_widget.height() <= 200
    block = panel._channels_block
    block_y = block.mapTo(panel, block.rect().topLeft()).y()
    label_y = label.mapTo(panel, label.rect().topLeft()).y()
    if block_y >= label_y + label.height():
        gap = block_y - (label_y + label.height())
        assert gap <= 16
    else:
        assert abs(label_y - block_y) <= 8


def test_inspector_sits_to_the_right_of_list(qtbot) -> None:
    panel = OperationsPanel()
    qtbot.addWidget(panel)
    op = _mute_op(channels=["FC"])
    _load_51(panel, [op])
    panel.resize(640, 400)
    panel.show()
    qtbot.waitExposed(panel)
    panel.update_operation_editor(op)
    assert panel._splitter.orientation() == Qt.Orientation.Horizontal
    assert panel._inspector.isVisible()
    assert panel._inspector.x() > panel._list.x()
    assert panel._inspector.width() >= 240
    assert panel._inspector_stack.currentWidget() is panel._editor


def test_inspector_column_stays_visible_with_empty_state(qtbot) -> None:
    panel = OperationsPanel()
    qtbot.addWidget(panel)
    panel.resize(640, 400)
    panel.show()
    qtbot.waitExposed(panel)

    panel.load_operations([])
    assert panel._inspector.isVisible()
    assert panel._inspector_stack.currentWidget() is panel._empty_state

    op = _mute_op(channels=["FC"])
    _load_51(panel, [op])
    assert panel._inspector.isVisible()
    assert panel._inspector_stack.currentWidget() is panel._empty_state

    panel.update_operation_editor(op)
    assert panel._inspector_stack.currentWidget() is panel._editor

    panel.clear_selection()
    assert panel._inspector.isVisible()
    assert panel._inspector_stack.currentWidget() is panel._empty_state


def test_beep_tone_combo_emits_frequency(qtbot) -> None:
    panel = OperationsPanel()
    qtbot.addWidget(panel)
    op = Operation.create(
        OperationType.BEEP,
        0,
        ["FC"],
        TimePoint(Fraction(1)),
        TimePoint(Fraction(2)),
        params={"beep_freq": 1000.0, "gain": 0.3},
    )
    _load_51(panel, [op])
    panel.update_operation_editor(op)
    assert _row_visible(panel, panel._beep_combo)
    with qtbot.waitSignal(panel.censor_params_changed, timeout=1000) as blocker:
        panel._beep_combo.setCurrentIndex(panel._beep_combo.findData(800.0))
    assert blocker.args[0] == op.id
    assert blocker.args[1] == OperationType.BEEP
    assert blocker.args[2].beep_freq == 800.0
