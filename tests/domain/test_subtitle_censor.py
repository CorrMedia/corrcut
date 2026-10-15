"""SubtitleSettings serialization."""

from __future__ import annotations

from corrcut.domain.subtitle_settings import SubtitleSettings


def test_subtitle_settings_round_trip() -> None:
    settings = SubtitleSettings(
        stream_index=5,
        append_edited=True,
        track_name="Edited",
        replace_existing_edited_track=False,
    )
    restored = SubtitleSettings.from_json(settings.to_json())
    assert restored == settings


def test_subtitle_settings_legacy_enabled_maps_to_append_edited() -> None:
    restored = SubtitleSettings.from_json({"enabled": True})
    assert restored.append_edited is True


def test_subtitle_settings_append_generated_defaults_off() -> None:
    assert SubtitleSettings().append_generated is False
    assert SubtitleSettings.from_json({}).append_generated is False
    assert SubtitleSettings.from_json({"append_generated": True}).append_generated is True
