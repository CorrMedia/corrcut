from corrcut.domain.channel_layout import known_layout_channel_labels, layout_channel_labels


def test_known_layout_51() -> None:
    labels = known_layout_channel_labels("5.1(side)", 6)
    assert labels is not None
    assert "FC" in labels


def test_labels_match_ffmpeg_layout_decomposition() -> None:
    # Must match FFmpeg's actual channel decomposition (ffmpeg -layouts) so the
    # per-channel split/join extracts channels that exist in the source.
    assert known_layout_channel_labels("5.1", 6) == ["FL", "FR", "FC", "LFE", "BL", "BR"]
    assert known_layout_channel_labels("5.1(side)", 6) == ["FL", "FR", "FC", "LFE", "SL", "SR"]
    assert known_layout_channel_labels("6.1", 7) == ["FL", "FR", "FC", "LFE", "BC", "SL", "SR"]
    assert known_layout_channel_labels("7.1", 8) == [
        "FL",
        "FR",
        "FC",
        "LFE",
        "BL",
        "BR",
        "SL",
        "SR",
    ]
    assert known_layout_channel_labels("7.1(wide)", 8) == [
        "FL",
        "FR",
        "FC",
        "LFE",
        "BL",
        "BR",
        "FLC",
        "FRC",
    ]
    assert known_layout_channel_labels("7.1(wide-side)", 8) == [
        "FL",
        "FR",
        "FC",
        "LFE",
        "FLC",
        "FRC",
        "SL",
        "SR",
    ]


def test_default_layout_for_channels_uses_surround_or_stereo() -> None:
    from corrcut.domain.channel_layout import default_layout_for_channels

    assert default_layout_for_channels(8) == "7.1"
    assert default_layout_for_channels(2) == "stereo"
    assert default_layout_for_channels(0) == "stereo"
    assert default_layout_for_channels(6) == "5.1"


def test_unknown_layout_fallback() -> None:
    labels = layout_channel_labels("quad", 4)
    assert labels == ["CH1", "CH2", "CH3", "CH4"]
