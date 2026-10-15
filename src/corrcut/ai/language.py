"""Map container/ffprobe language tags to faster-whisper ISO 639-1 codes."""

from __future__ import annotations

# ffprobe/Matroska often use ISO 639-2/B (3-letter); Whisper expects ISO 639-1 (2-letter).
_ISO639_2B_TO_WHISPER: dict[str, str] = {
    "eng": "en",
    "en": "en",
    "spa": "es",
    "es": "es",
    "fre": "fr",
    "fra": "fr",
    "fr": "fr",
    "deu": "de",
    "ger": "de",
    "de": "de",
    "ita": "it",
    "it": "it",
    "por": "pt",
    "pt": "pt",
    "rus": "ru",
    "ru": "ru",
    "jpn": "ja",
    "ja": "ja",
    "zho": "zh",
    "chi": "zh",
    "zh": "zh",
    "yue": "yue",
    "kor": "ko",
    "ko": "ko",
    "nld": "nl",
    "dut": "nl",
    "nl": "nl",
    "swe": "sv",
    "sv": "sv",
    "nor": "no",
    "no": "no",
    "dan": "da",
    "da": "da",
    "fin": "fi",
    "fi": "fi",
    "pol": "pl",
    "pl": "pl",
    "ces": "cs",
    "cze": "cs",
    "cs": "cs",
    "hun": "hu",
    "hu": "hu",
    "tur": "tr",
    "tr": "tr",
    "ara": "ar",
    "ar": "ar",
    "hin": "hi",
    "hi": "hi",
    "heb": "he",
    "he": "he",
    "ukr": "uk",
    "uk": "uk",
    "ell": "el",
    "gre": "el",
    "el": "el",
    "ron": "ro",
    "rum": "ro",
    "ro": "ro",
    "ind": "id",
    "id": "id",
    "msa": "ms",
    "may": "ms",
    "ms": "ms",
    "tha": "th",
    "th": "th",
    "vie": "vi",
    "vi": "vi",
}

# Undefined / generic — let Whisper auto-detect.
_AUTO_DETECT = frozenset({"und", "unknown", "qaa", "qad"})


def whisper_language_code(raw: str | None) -> str | None:
    """
    Return a faster-whisper language code, or None to auto-detect.

    ffprobe tags like ``eng`` are mapped to ``en``.
    """
    if raw is None:
        return None
    code = raw.strip().lower().replace("_", "-")
    if not code:
        return None
    # BCP-47 style en-US → en
    if "-" in code:
        code = code.split("-", 1)[0]
    if code in _AUTO_DETECT:
        return None
    mapped = _ISO639_2B_TO_WHISPER.get(code)
    if mapped is not None:
        return mapped
    if len(code) == 2:
        return code
    return None
