from corrcut.ui.timeline.timecode import format_timecode, parse_timecode


def test_format_timecode() -> None:
    assert format_timecode(3661.5) == "01:01:01.500"


def test_parse_plain_seconds() -> None:
    assert parse_timecode("12.5") == 12.5


def test_parse_hms_mmm() -> None:
    assert parse_timecode("01:01:01.500") == 3661.5


def test_parse_mm_ss() -> None:
    assert parse_timecode("01:30") == 90.0


def test_parse_invalid() -> None:
    assert parse_timecode("not-a-time") is None
