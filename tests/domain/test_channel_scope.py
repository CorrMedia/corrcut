from corrcut.domain.channel_scope import (
    checks_from_scope_channels,
    editable_channel_names,
    operation_targets_channel,
    scope_channels_from_checks,
)


def test_editable_channel_names_51() -> None:
    assert editable_channel_names("5.1", 6) == ["FC", "FL", "FR", "BL", "BR"]


def test_editable_channel_names_51_side() -> None:
    assert editable_channel_names("5.1(side)", 6) == ["FC", "FL", "FR", "SL", "SR"]


def test_editable_channel_names_unlabeled_7_1() -> None:
    names = editable_channel_names(None, 8)
    assert names[:3] == ["FC", "FL", "FR"]
    assert "SL" in names
    assert "SR" in names
    assert "FL" in names
    assert "FR" in names


def test_scope_channels_from_checks() -> None:
    assert scope_channels_from_checks(FC=True, FL=False, FR=True) == ["FC", "FR"]


def test_checks_from_scope_channels() -> None:
    assert checks_from_scope_channels(
        ["FC", "BL"],
        ["FC", "FL", "FR", "BL", "BR"],
    ) == {
        "FC": True,
        "FL": False,
        "FR": False,
        "BL": True,
        "BR": False,
    }


def test_operation_targets_channel() -> None:
    assert operation_targets_channel(["FC"], "FC", per_channel_editing=True)
    assert not operation_targets_channel(["FC"], "FL", per_channel_editing=True)
    assert not operation_targets_channel([], "FC", per_channel_editing=True)
    assert operation_targets_channel([], "FL", per_channel_editing=False)
