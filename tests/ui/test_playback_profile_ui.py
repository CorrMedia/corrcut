"""Playback profile popup, preview filtering, and timeline dimming."""

from fractions import Fraction
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QCheckBox, QPushButton

from corrcut.domain.operation import Operation, OperationType
from corrcut.domain.project import Project, SourceFileRecord
from corrcut.domain.time_point import TimePoint
from corrcut.preview.playback_profile import PlaybackProfile
from corrcut.ui.playback_profile_popup import PlaybackProfilePopup
from corrcut.ui.preview_panel import PreviewPanel
from corrcut.ui.timeline.region_item import RegionItem
from corrcut.ui.timeline.timeline_widget import TimelineWidget

_app = QApplication.instance() or QApplication([])


def test_unchecking_a_group_turns_off_its_words(qtbot) -> None:
    popup = PlaybackProfilePopup(PlaybackProfile.full())
    qtbot.addWidget(popup)
    seen: list[PlaybackProfile] = []
    popup.profile_changed.connect(seen.append)

    profanity = popup.findChild(QCheckBox, "playback-cat-profanity")
    fuck = popup.findChild(QCheckBox, "playback-cat-word_fuck")
    assert profanity is not None and fuck is not None
    assert profanity.checkState() == Qt.CheckState.Checked

    profanity.click()
    assert seen
    profile = seen[-1]
    assert "profanity" in profile.disabled_categories
    assert "word_fuck" in profile.disabled_categories
    assert fuck.checkState() == Qt.CheckState.Unchecked
    assert profanity.checkState() == Qt.CheckState.Unchecked

    restore = popup.findChild(QPushButton, "playbackProfileAll")
    assert restore is not None
    restore.click()
    assert seen[-1].is_full()
    assert profanity.checkState() == Qt.CheckState.Checked


def test_unchecking_one_word_leaves_the_group_partial(qtbot) -> None:
    popup = PlaybackProfilePopup(PlaybackProfile.full())
    qtbot.addWidget(popup)
    seen: list[PlaybackProfile] = []
    popup.profile_changed.connect(seen.append)

    fuck = popup.findChild(QCheckBox, "playback-cat-word_fuck")
    profanity = popup.findChild(QCheckBox, "playback-cat-profanity")
    shit = popup.findChild(QCheckBox, "playback-cat-word_shit")
    assert fuck is not None and profanity is not None and shit is not None

    fuck.click()
    profile = seen[-1]
    assert "word_fuck" in profile.disabled_categories
    assert "word_shit" not in profile.disabled_categories
    assert "profanity" in profile.disabled_categories
    assert profanity.checkState() == Qt.CheckState.PartiallyChecked
    assert shit.checkState() == Qt.CheckState.Checked
    assert profile.matches(
        Operation.create(
            OperationType.MUTE,
            1,
            [],
            TimePoint(Fraction(0)),
            TimePoint(Fraction(1)),
            categories=["profanity"],
        )
    )


def test_unchecking_an_action_drops_that_action(qtbot) -> None:
    popup = PlaybackProfilePopup(PlaybackProfile.full())
    qtbot.addWidget(popup)
    seen: list[PlaybackProfile] = []
    popup.profile_changed.connect(seen.append)
    skip = popup.findChild(QCheckBox, "playback-action-skip")
    assert skip is not None
    skip.click()
    assert "skip" in seen[-1].disabled_actions
    assert seen[-1].button_label() == "Custom"


def test_preview_profile_filters_skip_ranges(qtbot) -> None:
    panel = PreviewPanel()
    qtbot.addWidget(panel)
    project = Project(
        source=SourceFileRecord(
            path=Path("missing-movie.mkv"),
            fingerprint={},
            probe={"duration_seconds": 20.0},
        ),
        operations=[
            Operation.create(
                OperationType.SKIP,
                -1,
                [],
                TimePoint(Fraction(5)),
                TimePoint(Fraction(8)),
                categories=["word_fuck"],
            )
        ],
    )
    panel.set_project(project)
    assert panel._skip_ranges == [(5.0, 8.0)]  # noqa: SLF001

    panel.set_playback_profile(PlaybackProfile.full().without(categories={"word_fuck"}))
    assert panel._skip_ranges == []  # noqa: SLF001

    panel.set_edits_enabled(False)
    panel.set_playback_profile(PlaybackProfile.full())
    assert panel._skip_ranges == []  # noqa: SLF001
    panel.set_edits_enabled(True)
    assert panel._skip_ranges == [(5.0, 8.0)]  # noqa: SLF001


def test_excluded_region_is_dimmer_but_still_editable() -> None:
    item = RegionItem(
        "op-1",
        OperationType.MUTE,
        2.0,
        5.0,
        pixels_per_second=80.0,
        track_height=120.0,
        scroll_x=0.0,
        duration_s=20.0,
    )
    before = item.brush().color().alpha()
    item.set_playback_included(False)
    assert item.brush().color().alpha() < before
    assert item.edit_enabled


def test_timeline_dims_only_while_edited_playback_uses_the_profile(qtbot) -> None:
    timeline = TimelineWidget()
    qtbot.addWidget(timeline)
    op = Operation.create(
        OperationType.MUTE,
        1,
        [],
        TimePoint(Fraction(0)),
        TimePoint(Fraction(1)),
        categories=["word_fuck"],
    )
    timeline.set_duration(10.0)
    timeline.sync_operations([op])
    item = timeline._regions[op.id]  # noqa: SLF001
    assert item.playback_included()

    narrowed = PlaybackProfile.full().without(categories={"word_fuck"})
    timeline.set_playback_filter(edits_on=True, profile=narrowed)
    assert not item.playback_included()

    timeline.set_playback_filter(edits_on=False, profile=narrowed)
    assert item.playback_included()
