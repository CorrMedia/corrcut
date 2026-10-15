"""Display punctuation stripping for suggestion words."""

from corrcut.ai.normalize import strip_display_punctuation


def test_strip_display_punctuation_keeps_hyphens_and_apostrophes() -> None:
    assert strip_display_punctuation("shit,") == "shit"
    assert strip_display_punctuation("damn.") == "damn"
    assert strip_display_punctuation('"hell!"') == "hell"
    assert strip_display_punctuation("son of a bitch,") == "son of a bitch"
    assert strip_display_punctuation("well-known") == "well-known"
    assert strip_display_punctuation("fuckin'") == "fuckin'"
    assert strip_display_punctuation("fuckin’") == "fuckin’"
