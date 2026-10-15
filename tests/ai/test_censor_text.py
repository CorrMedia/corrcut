"""Subtitle/text censorship tests."""

from __future__ import annotations

from corrcut.ai.censor_text import censor_subtitle_line, censor_text, contains_profanity


def test_censor_text_replaces_profanity() -> None:
    result = censor_text("What the fuck was that?", replacement="***")
    assert "fuck" not in result.lower()
    assert "***" in result


def test_censor_text_preserves_clean_words() -> None:
    result = censor_text("Hello there friend", replacement="***")
    assert result == "Hello there friend"


def test_contains_profanity() -> None:
    assert contains_profanity("What the fuck was that?")
    assert not contains_profanity("Hello there friend")


def test_censor_subtitle_line_handles_multiline() -> None:
    result = censor_subtitle_line("Line one shit\nLine two", replacement="***")
    assert "shit" not in result.lower()
    assert "\n" in result


def test_censor_text_replaces_shitting() -> None:
    result = censor_text("He was shitting himself", replacement="***")
    assert "shitting" not in result.lower()
    assert "***" in result


def test_censor_text_replaces_bitching() -> None:
    result = censor_text("That ride was bitching", replacement="***")
    assert "bitching" not in result.lower()
    assert "***" in result


def test_censor_text_replaces_bitchin() -> None:
    result = censor_text("That ride was bitchin", replacement="***")
    assert "bitchin" not in result.lower()
    assert "***" in result


def test_censor_text_replaces_bullshitting() -> None:
    result = censor_text("That is bullshitting", replacement="***")
    assert "bullshitting" not in result.lower()
    assert "***" in result
