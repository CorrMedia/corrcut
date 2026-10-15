"""Phase 1 probe parsing and cache."""

from pathlib import Path

from corrcut.engine.probe import (
    probe_result_from_dict,
    probe_result_from_ffprobe_json,
    probe_result_to_dict,
)
from corrcut.io.probe_cache import fingerprint_cache_key, load_probe_cache, save_probe_cache


def test_probe_parses_video_audio_subtitle() -> None:
    data = {
        "format": {"format_name": "matroska,webm", "duration": "120.5"},
        "streams": [
            {
                "index": 0,
                "codec_type": "video",
                "codec_name": "h264",
                "width": 720,
                "height": 480,
                "sample_aspect_ratio": "853:720",
                "display_aspect_ratio": "853:480",
                "r_frame_rate": "24000/1001",
                "tags": {"rotate": "-90"},
            },
            {
                "index": 1,
                "codec_type": "audio",
                "codec_name": "ac3",
                "channels": 6,
                "channel_layout": "5.1(side)",
                "sample_rate": "48000",
                "tags": {"language": "eng", "title": "Main"},
                "disposition": {"default": 1},
            },
            {
                "index": 3,
                "codec_type": "audio",
                "codec_name": "aac",
                "channels": 2,
                "channel_layout": "stereo",
                "sample_rate": "48000",
                "tags": {"language": "eng", "title": "Commentary"},
                "disposition": {"default": 0},
            },
            {
                "index": 2,
                "codec_type": "subtitle",
                "codec_name": "subrip",
                "tags": {"language": "eng", "title": "English"},
            },
        ],
    }
    result = probe_result_from_ffprobe_json(Path("movie.mkv"), data)
    assert len(result.video_streams) == 1
    video = result.video_streams[0]
    assert video.frame_rate == "24000/1001"
    assert video.sample_aspect_ratio == "853:720"
    assert video.display_aspect_ratio == "853:480"
    assert video.rotation == 270
    restored = probe_result_from_dict(probe_result_to_dict(result))
    assert restored.video_streams[0].display_aspect_ratio == "853:480"
    assert restored.video_streams[0].rotation == 270
    assert len(result.audio_streams) == 2
    main, commentary = result.audio_streams
    assert main.title == "Main"
    assert main.is_default is True
    assert commentary.title == "Commentary"
    assert commentary.is_default is False
    assert main.dialogue_target_strategy.value == "center_channel"
    assert len(result.subtitle_streams) == 1
    assert result.subtitle_streams[0].title == "English"


def test_probe_dict_round_trip() -> None:
    data = {
        "format": {"format_name": "matroska", "duration": "10"},
        "streams": [
            {
                "index": 0,
                "codec_type": "audio",
                "codec_name": "aac",
                "channels": 2,
                "channel_layout": "stereo",
                "sample_rate": "48000",
            },
        ],
    }
    original = probe_result_from_ffprobe_json(Path("x.mkv"), data)
    restored = probe_result_from_dict(probe_result_to_dict(original))
    assert restored.format_name == original.format_name
    assert len(restored.audio_streams) == 1
    assert restored.audio_streams[0].channel_layout == "stereo"


def test_audio_default_fallback_when_no_disposition() -> None:
    data = {
        "format": {"format_name": "matroska", "duration": "10"},
        "streams": [
            {
                "index": 0,
                "codec_type": "audio",
                "codec_name": "aac",
                "channels": 2,
                "channel_layout": "stereo",
            },
            {
                "index": 1,
                "codec_type": "audio",
                "codec_name": "aac",
                "channels": 2,
                "channel_layout": "stereo",
            },
        ],
    }
    result = probe_result_from_ffprobe_json(Path("x.mkv"), data)
    assert result.audio_streams[0].is_default is True
    assert result.audio_streams[1].is_default is False


def test_track_list_label() -> None:
    from corrcut.domain.dialogue_target import DialogueTargetStrategy
    from corrcut.domain.project import TrackRecord

    track = TrackRecord(
        stream_index=1,
        codec="ac3",
        channel_layout="5.1(side)",
        channels=6,
        sample_rate=48000,
        language="eng",
        dialogue_target_strategy=DialogueTargetStrategy.CENTER_CHANNEL,
        title="Director's Commentary",
        is_default=False,
    )
    assert track.list_label() == "#1 ac3 5.1(side) (eng) — Director's Commentary"

    default = TrackRecord(
        stream_index=2,
        codec="aac",
        channel_layout="stereo",
        channels=2,
        sample_rate=48000,
        language=None,
        dialogue_target_strategy=DialogueTargetStrategy.FULL_MIX,
        is_default=True,
    )
    assert default.list_label() == "#2 aac stereo [default]"


def test_probe_cache_by_fingerprint(tmp_path: Path) -> None:
    fp = {"size": 999, "head_sha256": "abc", "tail_sha256": "def"}
    data = {
        "format": {"format_name": "matroska", "duration": "1"},
        "streams": [],
    }
    probe = probe_result_from_ffprobe_json(Path("cached.mkv"), data)
    save_probe_cache(tmp_path, fp, probe)
    assert fingerprint_cache_key(fp) in fingerprint_cache_key(fp)
    loaded = load_probe_cache(tmp_path, fp)
    assert loaded is not None
    assert loaded.format_name == "matroska"
