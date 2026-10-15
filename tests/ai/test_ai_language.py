"""ffprobe → Whisper language tag normalization."""

from corrcut.ai.language import whisper_language_code


def test_eng_maps_to_en() -> None:
    assert whisper_language_code("eng") == "en"


def test_en_passthrough() -> None:
    assert whisper_language_code("en") == "en"


def test_und_auto_detect() -> None:
    assert whisper_language_code("und") is None


def test_bcp47_primary_subtag() -> None:
    assert whisper_language_code("en-US") == "en"
