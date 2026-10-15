"""Heuristic cleanup for raw OCR subtitle text."""

from __future__ import annotations

import re

from corrcut.ai.subtitles import SubtitleCue

# Characters Tesseract often misplaces at word or line edges.
_EDGE_JUNK = frozenset("|\\/`~_+*@$%^&")

# Horizontal whitespace, including common OCR oddities.
_SPACE_RE = re.compile(r"[\t\f\v\u00a0\u2000-\u200b\u202f\u205f\u3000]+")

_DIALOGUE_DASH = "-"
_DIALOGUE_SPLIT_RE = re.compile(r"\s(?:=|[«‹»›])\s+")
_LINE_START_DIALOGUE_RE = re.compile(r"^[=<|«‹]\s*")
_LINE_START_DIALOGUE_GLUED_RE = re.compile(r"^[=<|«‹](?=[^\s-])")

# Common single-word OCR misreads in English subtitles.
_COMMON_OCR_WORD_FIXES: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"\bFm\b"), "I'm"),
    (re.compile(r"\bIm\b"), "I'm"),
    (re.compile(r"\bIrn\b"), "I'm"),
    (re.compile(r"\baiso\b", re.IGNORECASE), "also"),
    (re.compile(r"\bAIso\b"), "Also"),
)

_MUSIC_NOTE = "\u266a"  # ♪
_MUSIC_NOTES = "\u266b"  # ♫

# Standalone tokens that are common OCR misreads of one music note.
_MUSIC_SINGLE_TOKENS = frozenset({"#", "\u266f", "&", "*", "'", "`"})
_MUSIC_DOUBLE_TOKENS = frozenset({"##", "&&", "**", "\u266a\u266a", _MUSIC_NOTES})

# Subtitle Edit / Tesseract often emit J with a quote for ♪.
_MUSIC_JUNK_TOKEN_RE = re.compile(r'^J["\u201c\u201d\']$')

# Bracketing markers at line edges, e.g. "# Hello there #".
_LINE_START_MUSIC_RE = re.compile(r"^#+\s*")
_LINE_END_MUSIC_RE = re.compile(r"\s*#+$")
_LINE_START_AMP_RE = re.compile(r"^&+\s*")
_LINE_END_AMP_RE = re.compile(r"\s*&+$")

# Glued markers, e.g. "#Hello" or "there#".
_GLUED_START_MUSIC_RE = re.compile(r"^#+(?=\S)")
_GLUED_END_MUSIC_RE = re.compile(r"(?<=\S)#+$")


def clean_ocr_text(text: str) -> str:
    """Normalize spacing and strip common OCR edge artifacts from subtitle text."""
    if not text:
        return text

    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    normalized = _normalize_dialogue_dashes_text(normalized)
    lines = [_clean_ocr_line(line) for line in normalized.split("\n")]
    return "\n".join(line for line in lines if line)


def clean_ocr_cues(cues: list[SubtitleCue]) -> list[SubtitleCue]:
    """Apply :func:`clean_ocr_text` to each cue, dropping cues that become empty."""
    cleaned: list[SubtitleCue] = []
    for cue in cues:
        text = clean_ocr_text(cue.text)
        if _is_empty_cleanup_result(text):
            continue
        if text == cue.text:
            cleaned.append(cue)
            continue
        cleaned.append(
            SubtitleCue(
                start=cue.start,
                end=cue.end,
                text=text,
            )
        )
    return cleaned


def _normalize_dialogue_dashes_text(text: str) -> str:
    expanded: list[str] = []
    for line in text.split("\n"):
        expanded.extend(_expand_dialogue_merged_line(line))
    return "\n".join(expanded)


def _expand_dialogue_merged_line(line: str) -> list[str]:
    line = line.strip()
    if not line or _is_subtitle_tag_line(line):
        return [line] if line else []

    tag_only = _extract_trailing_subtitle_tag(line)
    if tag_only is not None:
        return [tag_only]

    line = _LINE_START_DIALOGUE_RE.sub(f"{_DIALOGUE_DASH} ", line)
    line = _LINE_START_DIALOGUE_GLUED_RE.sub(f"{_DIALOGUE_DASH} ", line)

    parts = _DIALOGUE_SPLIT_RE.split(line)
    if len(parts) < 2 or not _looks_like_dialogue_split(parts):
        return [line]

    formatted: list[str] = []
    for part in parts:
        part = part.strip()
        if not part:
            return [line]
        formatted.append(_ensure_dialogue_dash_prefix(part))
    return formatted


def _looks_like_dialogue_split(parts: list[str]) -> bool:
    for part in parts[1:]:
        stripped = part.lstrip()
        if not stripped:
            return False
        if stripped[0] in (_DIALOGUE_DASH, "\u2013", "\u2014", "\u00ab", "\u00bb", "\u2039", "\u203a"):
            continue
        if not stripped[0].isalpha():
            return False
    return True


def _ensure_dialogue_dash_prefix(text: str) -> str:
    if text.startswith(f"{_DIALOGUE_DASH} "):
        return text
    if text.startswith(_DIALOGUE_DASH):
        return f"{_DIALOGUE_DASH} {text[1:].lstrip()}"
    if text.startswith(("\u2013", "\u2014", "\u00ab", "\u00bb", "\u2039", "\u203a")):
        return f"{_DIALOGUE_DASH} {text[1:].lstrip()}"
    return f"{_DIALOGUE_DASH} {text}"


def _clean_ocr_line(line: str) -> str:
    line = _SPACE_RE.sub(" ", line).strip()
    if not line:
        return ""

    line = _normalize_music_symbols(line)
    line = _strip_edge_junk(line)
    words = line.split(" ")
    cleaned_words = [_strip_word_junk(word) for word in words if word]
    line = " ".join(word for word in cleaned_words if word)
    return _apply_common_word_fixes(line)


def _apply_common_word_fixes(line: str) -> str:
    updated = line
    for pattern, replacement in _COMMON_OCR_WORD_FIXES:
        updated = pattern.sub(replacement, updated)
    return updated


def _normalize_music_symbols(line: str) -> str:
    if _is_subtitle_tag_line(line):
        return line

    line = _normalize_unicode_music_chars(line)
    line = _normalize_bracketing_music_markers(line)

    words = line.split(" ")
    normalized_words = [_normalize_music_token(word) for word in words if word]
    return " ".join(normalized_words)


def _is_subtitle_tag_line(line: str) -> bool:
    stripped = line.strip()
    return stripped.startswith("[") and stripped.endswith("]")


def _extract_trailing_subtitle_tag(line: str) -> str | None:
    match = re.search(r"(\[[^\]]+\])\s*$", line)
    if match is None:
        return None
    prefix = line[: match.start()].strip()
    if not prefix:
        return match.group(1)
    if all(char in _EDGE_JUNK or char in "=|«‹»›" for char in prefix.replace(" ", "")):
        return match.group(1)
    return None


def _is_empty_cleanup_result(text: str) -> bool:
    stripped = text.strip()
    return not stripped or stripped == _DIALOGUE_DASH


def _normalize_unicode_music_chars(text: str) -> str:
    return (
        text.replace("\u2669", _MUSIC_NOTE)  # ♩
        .replace("\u266c", _MUSIC_NOTES)  # ♬
        .replace("\u266f", _MUSIC_NOTE)  # ♯ (often a misread note, not a sharp)
    )


def _normalize_bracketing_music_markers(line: str) -> str:
    stripped = line.strip()
    if re.fullmatch(r"#+", stripped):
        return _MUSIC_NOTES if len(stripped) >= 2 else _MUSIC_NOTE
    if re.fullmatch(r"&+", stripped):
        return _MUSIC_NOTES if len(stripped) >= 2 else _MUSIC_NOTE

    updated = line
    if _LINE_START_MUSIC_RE.match(updated):
        updated = _LINE_START_MUSIC_RE.sub(f"{_MUSIC_NOTE} ", updated, count=1)
    if _LINE_END_MUSIC_RE.search(updated):
        updated = _LINE_END_MUSIC_RE.sub(f" {_MUSIC_NOTE}", updated, count=1)

    if _LINE_START_AMP_RE.match(updated):
        updated = _LINE_START_AMP_RE.sub(f"{_MUSIC_NOTE} ", updated, count=1)
    if _LINE_END_AMP_RE.search(updated):
        updated = _LINE_END_AMP_RE.sub(f" {_MUSIC_NOTE}", updated, count=1)

    if _GLUED_START_MUSIC_RE.match(updated):
        updated = _GLUED_START_MUSIC_RE.sub(f"{_MUSIC_NOTE} ", updated, count=1)
    if _GLUED_END_MUSIC_RE.search(updated):
        updated = _GLUED_END_MUSIC_RE.sub(f" {_MUSIC_NOTE}", updated, count=1)

    return updated


def _normalize_music_token(token: str) -> str:
    if not token or _is_subtitle_tag_line(token):
        return token

    if token in _MUSIC_DOUBLE_TOKENS:
        return _MUSIC_NOTES
    if token in _MUSIC_SINGLE_TOKENS:
        return _MUSIC_NOTE
    if _MUSIC_JUNK_TOKEN_RE.match(token):
        return _MUSIC_NOTE

    if all(char in f"{_MUSIC_NOTE}{_MUSIC_NOTES}\u2669\u266c" for char in token):
        if _MUSIC_NOTES in token or token.count(_MUSIC_NOTE) > 1:
            return _MUSIC_NOTES
        return _MUSIC_NOTE

    return token


def _strip_edge_junk(value: str) -> str:
    start = 0
    end = len(value)
    while start < end and value[start] in _EDGE_JUNK:
        start += 1
    while end > start and value[end - 1] in _EDGE_JUNK:
        end -= 1
    return value[start:end]


def _strip_word_junk(word: str) -> str:
    if not word:
        return word
    if word.startswith("[") and "]" in word:
        return word
    if word in (_MUSIC_NOTE, _MUSIC_NOTES):
        return word
    return _strip_edge_junk(word)
