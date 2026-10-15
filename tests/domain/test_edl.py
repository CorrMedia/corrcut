"""CorrMedia / Comskip EDL import."""

from fractions import Fraction

from corrcut.domain.edl import parse_edl_text, parse_edl_time
from corrcut.domain.operation import OperationType


def test_parse_edl_time_seconds_and_timecode() -> None:
    assert parse_edl_time("10.5") == 10.5
    assert parse_edl_time("1:30") == 90.0
    assert parse_edl_time("01:02:03.5") == 3723.5
    assert parse_edl_time("01:00:00:00") is None


def test_parse_mute_skip_and_default_skip() -> None:
    result = parse_edl_text(
        """
# mute then skip
0.0 10.0 1
15.0 20.0 3
30.0 40.0
        """,
        default_stream_index=1,
        default_channels=["FC"],
    )
    assert [op.type for op in result.operations] == [
        OperationType.MUTE,
        OperationType.SKIP,
        OperationType.SKIP,
    ]
    assert result.operations[0].scope.channels == ["FC"]
    assert result.operations[0].source == "edl"
    assert not result.warnings


def test_scene_marker_and_pause_are_unmapped() -> None:
    result = parse_edl_text(
        """
0.0 10.0 1
45.0 50.0 2
60.0 65.0 pause
        """,
        default_stream_index=0,
    )
    assert len(result.operations) == 1
    assert result.operations[0].type == OperationType.MUTE
    messages = [w.message for w in result.warnings]
    assert any("scene marker" in message for message in messages)
    assert any("pause" in message for message in messages)


def test_named_action_and_category() -> None:
    result = parse_edl_text(
        '10.0 12.0 mute profanity "center mute"\n20.0 21.0 beep word_fuck\n',
        default_stream_index=1,
        default_channels=["FL", "FR"],
    )
    assert len(result.operations) == 2
    mute = result.operations[0]
    assert mute.type == OperationType.MUTE
    assert mute.categories == ["profanity"]
    assert mute.description == "center mute"
    assert result.operations[1].categories == ["word_fuck"]
    assert not result.warnings


def test_unknown_category_stored_as_description() -> None:
    result = parse_edl_text(
        "10.0 12.0 mute commercial\n",
        default_stream_index=0,
    )
    assert len(result.operations) == 1
    assert result.operations[0].description == "commercial"
    assert result.operations[0].categories == ["other"]
    assert result.warnings
    assert "catalog category" in result.warnings[0].message


def test_spatial_named_action_warns_about_default_box() -> None:
    result = parse_edl_text(
        "5.0 8.0 cover\n",
        default_stream_index=0,
    )
    assert len(result.operations) == 1
    assert result.operations[0].type == OperationType.COVER
    assert result.warnings
    assert "default region" in result.warnings[0].message


def test_cmx_header_is_rejected() -> None:
    result = parse_edl_text(
        "TITLE: Test Movie\nFCM: NON-DROP FRAME\n",
        default_stream_index=0,
    )
    assert result.operations == []
    assert result.looks_like_cmx
    assert result.warnings


def test_comskip_cut_type_zero_maps_to_skip() -> None:
    result = parse_edl_text("100 110 0\n", default_stream_index=0)
    assert len(result.operations) == 1
    assert result.operations[0].type == OperationType.SKIP
    start = float(result.operations[0].time_range.start.seconds)
    assert abs(start - 100.0) < 1e-9
    assert isinstance(result.operations[0].time_range.start.seconds, Fraction)
