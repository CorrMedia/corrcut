"""CorrMedia-style playback profile: categories, actions, and saved settings."""

from fractions import Fraction
from pathlib import Path

from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication

from corrcut.domain.corr_categories import category_child_ids
from corrcut.domain.operation import Operation, OperationType
from corrcut.domain.project import Project
from corrcut.domain.time_point import TimePoint
from corrcut.preview.playback_profile import (
    PlaybackProfile,
    load_playback_profile,
    save_playback_profile,
)
from corrcut.preview.skip_playback import skip_ranges_from_project

_app = QApplication.instance() or QApplication([])


def _op(
    op_type: OperationType,
    *categories: str,
    enabled: bool = True,
) -> Operation:
    op = Operation.create(
        op_type,
        1,
        [],
        TimePoint(Fraction(1)),
        TimePoint(Fraction(2)),
        categories=list(categories) or None,
    )
    op.enabled = enabled
    return op


def test_full_profile_plays_an_enabled_edit() -> None:
    assert PlaybackProfile.full().matches(_op(OperationType.MUTE, "word_fuck"))
    assert PlaybackProfile.full().is_full()
    assert not PlaybackProfile.full().is_passthrough()


def test_disabled_edit_stays_out_of_preview() -> None:
    op = _op(OperationType.MUTE, "word_fuck", enabled=False)
    assert not PlaybackProfile.full().matches(op)


def test_word_opt_out_leaves_the_rest_of_the_group() -> None:
    profile = PlaybackProfile.full().without(categories={"word_fuck"})
    assert not profile.matches(_op(OperationType.MUTE, "word_fuck"))
    assert profile.matches(_op(OperationType.MUTE, "word_shit"))
    assert profile.matches(_op(OperationType.MUTE, "profanity"))


def test_group_tag_plays_when_any_word_in_the_group_is_on() -> None:
    enabled = PlaybackProfile.full().enabled_categories() - {"profanity", "word_fuck"}
    profile = PlaybackProfile.from_selection(
        categories=enabled,
        actions=PlaybackProfile.full().enabled_actions(),
    )
    assert not profile.matches(_op(OperationType.MUTE, "word_fuck"))
    assert profile.matches(_op(OperationType.MUTE, "profanity"))
    assert profile.matches(_op(OperationType.MUTE, "word_shit"))


def test_turning_a_whole_group_off_drops_its_words_and_the_group_tag() -> None:
    hidden = {"profanity", *category_child_ids("profanity")}
    profile = PlaybackProfile.full().without(categories=hidden)
    assert not profile.matches(_op(OperationType.MUTE, "profanity"))
    assert not profile.matches(_op(OperationType.MUTE, "word_fuck"))
    assert profile.matches(_op(OperationType.MUTE, "gore"))


def test_action_filter_is_independent_of_categories() -> None:
    profile = PlaybackProfile.full().without(actions={"skip"})
    assert profile.matches(_op(OperationType.MUTE, "gore"))
    assert not profile.matches(_op(OperationType.SKIP, "gore"))


def test_empty_profile_is_passthrough() -> None:
    profile = PlaybackProfile.none()
    assert profile.is_passthrough()
    assert profile.button_label() == "None"
    assert not profile.matches(_op(OperationType.MUTE, "gore"))


def test_a_word_listed_under_two_groups_is_one_switch() -> None:
    assert "word_goddamn" in category_child_ids("blasphemy")
    assert "word_goddamn" in category_child_ids("profanity")
    profile = PlaybackProfile.full().without(categories={"word_goddamn"})
    assert not profile.matches(_op(OperationType.MUTE, "word_goddamn"))
    assert profile.matches(_op(OperationType.MUTE, "word_fuck"))
    assert profile.matches(_op(OperationType.MUTE, "blasphemy"))


def test_skip_ranges_follow_the_profile() -> None:
    skip = _op(OperationType.SKIP, "word_fuck")
    project = Project(operations=[skip])
    assert skip_ranges_from_project(project) == [(1.0, 2.0)]
    narrowed = PlaybackProfile.full().without(actions={"skip"})
    assert skip_ranges_from_project(project, profile=narrowed) == []
    assert skip_ranges_from_project(project, profile=PlaybackProfile.full()) == [(1.0, 2.0)]


def test_profile_settings_round_trip(tmp_path: Path, monkeypatch) -> None:  # noqa: ANN001
    ini = tmp_path / "playback.ini"

    def _settings() -> QSettings:
        return QSettings(str(ini), QSettings.Format.IniFormat)

    monkeypatch.setattr("corrcut.config.app_settings", _settings)
    profile = PlaybackProfile.full().without(actions={"skip"}, categories={"word_fuck"})
    save_playback_profile(profile)
    assert load_playback_profile() == profile
    save_playback_profile(PlaybackProfile.full())
    assert load_playback_profile().is_full()


def test_unknown_saved_ids_are_ignored() -> None:
    raw = (
        '{"disabled_categories":["not_a_category","word_fuck"],'
        '"disabled_actions":["nope","skip"]}'
    )
    profile = PlaybackProfile.decode(raw)
    assert profile.disabled_categories == frozenset({"word_fuck"})
    assert profile.disabled_actions == frozenset({"skip"})
    assert PlaybackProfile.decode("").is_full()
    assert PlaybackProfile.decode("{").is_full()
