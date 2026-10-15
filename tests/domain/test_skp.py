"""VideoSkip .skp import."""

from pathlib import Path

from corrcut.domain.operation import OperationType
from corrcut.domain.skp import (
    load_skp_file,
    media_path_for_skp,
    parse_skp_text,
    parse_skp_time,
)


def test_parse_skp_time_hms_and_comma_decimals() -> None:
    assert parse_skp_time("10.5") == 10.5
    assert parse_skp_time("1:30") == 90.0
    assert parse_skp_time("0:14:08.27") == 848.27
    assert parse_skp_time("0:14:08,27") == 848.27


def test_docs_example_nude_image_is_blank() -> None:
    result = parse_skp_text(
        """
0:00:05.12
studio logo flash

0:14:08.27 --> 0:14:14
nude image 1 (male, from behind)
        """,
        default_stream_index=1,
        default_channels=["FC"],
    )
    assert len(result.operations) == 1
    op = result.operations[0]
    assert op.type == OperationType.BLANK
    assert op.categories == ["male_nudity"]
    assert op.description == "male, from behind"
    assert op.severity == 1
    assert op.source == "skp"
    start = float(op.time_range.start.seconds)
    end = float(op.time_range.end.seconds)
    assert abs(start - 848.27) < 1e-9
    assert abs(end - 854.0) < 1e-9
    assert not result.warnings


def test_profane_word_is_mute_with_word_leaf() -> None:
    result = parse_skp_text(
        "0:22:01 --> 0:22:03\nprofane word 1 (fuck)\n",
        default_stream_index=1,
        default_channels=["FL", "FR"],
    )
    assert len(result.operations) == 1
    mute = result.operations[0]
    assert mute.type == OperationType.MUTE
    assert mute.categories == ["word_fuck"]
    assert mute.description == "fuck"
    assert mute.severity == 1
    assert mute.scope.channels == ["FL", "FR"]
    assert mute.scope.stream_index == 1
    assert not result.warnings


def test_default_skip_and_violence_prefix() -> None:
    result = parse_skp_text(
        "0:30:00 --> 0:30:15\nviolence 2\n",
        default_stream_index=0,
    )
    assert len(result.operations) == 1
    skip = result.operations[0]
    assert skip.type == OperationType.SKIP
    assert skip.categories == ["gore", "graphic", "non_graphic"]
    assert skip.description is None
    assert skip.severity == 2


def test_blur_box_converts_percent_coords() -> None:
    result = parse_skp_text(
        "0:40:00 --> 0:40:10\nsex blur 1 [10,20,40,60]\n",
        default_stream_index=0,
    )
    assert len(result.operations) == 1
    blur = result.operations[0]
    assert blur.type == OperationType.BLUR
    assert blur.categories == ["sex_with_nudity"]
    assert blur.severity == 1
    assert blur.params["box"] == {"x": 0.1, "y": 0.2, "width": 0.3, "height": 0.4}
    assert not result.warnings


def test_full_frame_blur_without_box() -> None:
    result = parse_skp_text(
        "1:00:00 --> 1:00:05\nnudity blur 1\n",
        default_stream_index=0,
    )
    blur = result.operations[0]
    assert blur.type == OperationType.BLUR
    assert blur.params["box"] == {"x": 0.0, "y": 0.0, "width": 1.0, "height": 1.0}


def test_localized_blank_is_cover() -> None:
    result = parse_skp_text(
        "2:00 --> 2:05\nnude image 1 [0.2, 0.3, 0.5, 0.8]\n",
        default_stream_index=0,
    )
    cover = result.operations[0]
    assert cover.type == OperationType.COVER
    assert cover.params["box"] == {"x": 0.2, "y": 0.3, "width": 0.3, "height": 0.5}


def test_fast_maps_to_skip_with_warning() -> None:
    result = parse_skp_text(
        "1:00:00 --> 1:00:08\nscare fast 3 (jump scare)\n",
        default_stream_index=0,
    )
    assert result.operations[0].type == OperationType.SKIP
    assert result.operations[0].categories == ["objectionable_scary"]
    assert result.operations[0].description == "jump scare"
    assert any("fast" in warning.message for warning in result.warnings)


def test_commented_cue_imported_disabled() -> None:
    result = parse_skp_text(
        "0:50:00 --> 0:50:05\n// other 1 (commented)\n",
        default_stream_index=0,
    )
    assert len(result.operations) == 1
    assert not result.operations[0].enabled
    assert result.operations[0].description == "commented"
    assert any("//" in warning.message for warning in result.warnings)


def test_screenshot_and_offsets_are_stripped() -> None:
    result = parse_skp_text(
        "0:01:00 --> 0:01:02\nprofanity audio 1\n\n"
        '{"netflix": 1.25, "local": 0}\n\n'
        "data:image/jpeg;base64,/9j/4AAQSkZJRgABAQAAAQABAAD\n",
        default_stream_index=0,
    )
    assert len(result.operations) == 1
    assert result.operations[0].type == OperationType.MUTE
    assert result.operations[0].categories == ["profanity"]
    assert any("netflix" in warning.message for warning in result.warnings)


def test_unknown_category_stored_as_other() -> None:
    result = parse_skp_text(
        "10.0 --> 12.0\ncommercial 1\n",
        default_stream_index=0,
    )
    assert result.operations[0].categories == ["other"]
    assert any("category" in warning.message for warning in result.warnings)


def test_malformed_arrow_and_end_before_start() -> None:
    result = parse_skp_text(
        "0:10:00 -> 0:10:05\nsex 1\n\n0:20:00 --> 0:19:00\nviolence 1\n",
        default_stream_index=0,
    )
    assert len(result.operations) == 1
    assert result.operations[0].type == OperationType.SKIP
    assert any("end must be greater" in warning.message for warning in result.warnings)


def test_invalid_box_falls_back_to_full_frame() -> None:
    result = parse_skp_text(
        "0:10:00 --> 0:10:04\nnudity blur 1 [oops]\n",
        default_stream_index=0,
    )
    blur = result.operations[0]
    assert blur.type == OperationType.BLUR
    assert blur.params["box"] == {"x": 0.0, "y": 0.0, "width": 1.0, "height": 1.0}
    assert any("blur box" in warning.message for warning in result.warnings)


def test_empty_file_warns() -> None:
    result = parse_skp_text("", default_stream_index=0)
    assert result.operations == []
    assert result.warnings


def test_load_skp_file_and_sibling_media(tmp_path: Path) -> None:
    media = tmp_path / "movie.mkv"
    media.write_bytes(b"x")
    path = tmp_path / "movie.skp"
    path.write_text("0:00:01 --> 0:00:02\ncurse word 1 (damn)\n", encoding="utf-8")
    result = load_skp_file(path, default_stream_index=0)
    assert len(result.operations) == 1
    assert result.operations[0].categories == ["word_damn"]
    assert media_path_for_skp(path) == media
