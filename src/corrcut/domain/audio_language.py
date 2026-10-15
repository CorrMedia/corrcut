"""Language tag for an audio track, used to group corr.json audio edits."""

from __future__ import annotations

import re

DEFAULT_AUDIO_LANGUAGE = "eng"

# Container tags and ISO 639-1 codes collapse to the three-letter tag ffprobe writes.
_CANONICAL: dict[str, str] = {
    "en": "eng",
    "eng": "eng",
    "es": "spa",
    "spa": "spa",
    "fr": "fra",
    "fra": "fra",
    "fre": "fra",
    "de": "deu",
    "deu": "deu",
    "ger": "deu",
    "it": "ita",
    "ita": "ita",
    "pt": "por",
    "por": "por",
    "ru": "rus",
    "rus": "rus",
    "ja": "jpn",
    "jpn": "jpn",
    "zh": "zho",
    "zho": "zho",
    "chi": "zho",
    "ko": "kor",
    "kor": "kor",
    "nl": "nld",
    "nld": "nld",
    "dut": "nld",
    "sv": "swe",
    "swe": "swe",
    "no": "nor",
    "nor": "nor",
    "da": "dan",
    "dan": "dan",
    "fi": "fin",
    "fin": "fin",
    "pl": "pol",
    "pol": "pol",
    "cs": "ces",
    "ces": "ces",
    "cze": "ces",
    "hu": "hun",
    "hun": "hun",
    "tr": "tur",
    "tur": "tur",
    "ar": "ara",
    "ara": "ara",
    "hi": "hin",
    "hin": "hin",
    "he": "heb",
    "heb": "heb",
    "uk": "ukr",
    "ukr": "ukr",
    "el": "ell",
    "ell": "ell",
    "gre": "ell",
    "ro": "ron",
    "ron": "ron",
    "rum": "ron",
}

_UNDEFINED = frozenset({"und", "unknown", "qaa", "qad", "mis", "mul", "zxx"})

_TITLE_WORDS: dict[str, str] = {
    "english": "eng",
    "spanish": "spa",
    "español": "spa",
    "espanol": "spa",
    "french": "fra",
    "français": "fra",
    "francais": "fra",
    "german": "deu",
    "deutsch": "deu",
    "italian": "ita",
    "italiano": "ita",
    "portuguese": "por",
    "português": "por",
    "portugues": "por",
    "japanese": "jpn",
    "chinese": "zho",
    "korean": "kor",
    "russian": "rus",
}

LANGUAGE_CHOICES: tuple[tuple[str, str], ...] = (
    ("eng", "English"),
    ("spa", "Spanish"),
    ("fra", "French"),
    ("deu", "German"),
    ("ita", "Italian"),
    ("por", "Portuguese"),
    ("jpn", "Japanese"),
    ("zho", "Chinese"),
    ("kor", "Korean"),
    ("rus", "Russian"),
)

_LANGUAGE_NAMES = dict(LANGUAGE_CHOICES)


def normalize_audio_language(raw: str | None) -> str | None:
    """Return a three-letter language tag, or None when the tag is missing or undefined."""
    if raw is None:
        return None
    code = raw.strip().lower().replace("_", "-")
    if not code or code in _UNDEFINED:
        return None
    if "-" in code:
        code = code.split("-", 1)[0]
    if code in _UNDEFINED:
        return None
    mapped = _CANONICAL.get(code)
    if mapped is not None:
        return mapped
    if len(code) == 3 and code.isalpha():
        return code
    return None


def language_from_title(title: str | None) -> str | None:
    """Read an obvious language word from a track title."""
    if not title:
        return None
    for token in re.findall(r"[^\W\d_]+", title.lower(), flags=re.UNICODE):
        code = _TITLE_WORDS.get(token)
        if code is not None:
            return code
    return None


def resolve_track_language(
    language: str | None,
    title: str | None = None,
    *,
    override: str | None = None,
) -> tuple[str, bool]:
    """Return ``(tag, assumed)``. Assumed is true only for the English fallback."""
    forced = normalize_audio_language(override)
    if forced is not None:
        return forced, False
    tagged = normalize_audio_language(language)
    if tagged is not None:
        return tagged, False
    titled = language_from_title(title)
    if titled is not None:
        return titled, False
    return DEFAULT_AUDIO_LANGUAGE, True


def language_display_name(code: str, *, assumed: bool = False) -> str:
    name = _LANGUAGE_NAMES.get(code, code)
    if assumed:
        return f"{name} (assumed)"
    return name


def edit_language_label(
    language: str | None,
    *,
    track_language: tuple[str, bool] | None,
) -> str:
    """Name for an audio edit.

    ``(assumed)`` appears only while the edit still carries the English fallback
    from the track it was authored on.
    """
    code = normalize_audio_language(language)
    assumed = False
    if code is None:
        if track_language is not None:
            code, assumed = track_language
        else:
            code, assumed = resolve_track_language(None)
    elif track_language is not None and track_language[0] == code and track_language[1]:
        assumed = True
    return language_display_name(code, assumed=assumed)


def track_choice_label(
    *,
    language_name: str,
    layout: str,
    title: str | None,
    edit_count: int,
) -> str:
    bits = [language_name, layout or "audio"]
    if title:
        bits.append(title)
    noun = "edit" if edit_count == 1 else "edits"
    bits.append(f"{edit_count} {noun}")
    return " · ".join(bits)
