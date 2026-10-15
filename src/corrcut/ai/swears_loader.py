"""Load swear lists from ``swears/*.json`` at the repo root."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass

from corrcut.ai.inflection import inflected_forms
from corrcut.ai.language import whisper_language_code
from corrcut.ai.normalize import normalize_token
from corrcut.ai.phrase_match import MuteSpan, PhrasePattern, tokenize_phrase
from corrcut.config import env_value

_TOKEN_SPLIT = re.compile(r"[\s\-_/]+")


@dataclass(frozen=True)
class SwearEntry:
    """One row from a category JSON file (e.g. profanity.json)."""

    key: str
    category: str
    languages: frozenset[str]
    plural: bool
    forms: tuple[str, ...]
    phrases: tuple[PhrasePattern, ...]
    exceptions: frozenset[str]


def repo_swears_dir() -> Path:
    from pathlib import Path

    return Path(__file__).resolve().parents[3] / "swears"


def bundled_swears_dir() -> Path:
    from pathlib import Path

    return Path(__file__).resolve().parents[1] / "resources" / "swears"


def resolve_swears_dir() -> Path | None:
    from pathlib import Path

    override = env_value("SWEARS_DIR")
    if override:
        path = Path(override).expanduser()
        if path.is_dir():
            return path

    for candidate in (repo_swears_dir(), bundled_swears_dir(), Path.cwd() / "swears"):
        if candidate.is_dir() and any(candidate.glob("*.json")):
            return candidate
    return None


def normalize_language_tag(raw: str) -> str:
    """Normalize tags like ``eng``, ``en-US`` for entry/track comparison."""
    tag = raw.strip().lower().replace("_", "-")
    if not tag:
        return tag
    whisper = whisper_language_code(tag)
    if whisper and len(tag) <= 3:
        return whisper
    return tag


def languages_compatible(entry_lang: str, track_lang: str) -> bool:
    if entry_lang == track_lang:
        return True
    entry_base = entry_lang.split("-", 1)[0]
    track_base = track_lang.split("-", 1)[0]
    return entry_base == track_base


def entry_applies_to_language(entry: SwearEntry, track_language: str | None) -> bool:
    if not entry.languages:
        return True
    if not track_language:
        return True
    track_norm = normalize_language_tag(track_language)
    return any(languages_compatible(lang, track_norm) for lang in entry.languages)


def filter_entries_by_language(
    entries: list[SwearEntry],
    track_language: str | None,
) -> list[SwearEntry]:
    return [entry for entry in entries if entry_applies_to_language(entry, track_language)]


def _parse_languages(item: dict) -> frozenset[str]:
    raw = item.get("language")
    if raw is None:
        return frozenset()
    tags: list[str] = []
    if isinstance(raw, str):
        tags = [raw]
    elif isinstance(raw, list):
        tags = [str(t) for t in raw if isinstance(t, str)]
    return frozenset(normalize_language_tag(t) for t in tags if t.strip())


def _parse_mute_span(item: dict) -> MuteSpan:
    """``filter_constituents: true`` → mute full phrase; ``false`` → trigger word only."""
    if item.get("filter_constituents") is True:
        return "phrase"
    return "trigger"


def _parse_phrase_object(
    raw: object,
    *,
    canonical: str,
    category: str,
    default_mute_span: MuteSpan,
    exceptions: frozenset[str],
) -> PhrasePattern | None:
    if isinstance(raw, str):
        text = raw.strip()
        mute_span = default_mute_span
    elif isinstance(raw, dict):
        text = str(raw.get("text", "")).strip()
        span_raw = raw.get("mute_span")
        if span_raw == "phrase":
            mute_span = "phrase"
        elif span_raw == "trigger":
            mute_span = "trigger"
        else:
            mute_span = default_mute_span
    else:
        return None
    tokens = tokenize_phrase(text)
    if len(tokens) < 2:
        return None
    return PhrasePattern(
        tokens=tokens,
        canonical=canonical,
        category=category,
        mute_span=mute_span,
        exceptions=exceptions,
        raw=text,
    )


def load_swear_entries(swears_dir: Path | None = None) -> list[SwearEntry]:

    directory = swears_dir or resolve_swears_dir()
    if directory is None:
        return []

    entries: list[SwearEntry] = []
    for path in sorted(directory.glob("*.json")):
        category = path.stem
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        if not isinstance(raw, list):
            continue
        for item in raw:
            if not isinstance(item, dict):
                continue
            key = item.get("key")
            if not key or not isinstance(key, str):
                continue

            canonical = key.strip()
            exceptions_raw = item.get("exceptions", [])
            exceptions = frozenset(
                str(e).strip().lower()
                for e in exceptions_raw
                if isinstance(e, str) and str(e).strip()
            )
            default_mute_span = _parse_mute_span(item)
            plural = bool(item.get("plural", False))
            languages = _parse_languages(item)

            alias_forms: list[str] = []
            phrases: list[PhrasePattern] = []

            forms_raw = item.get("forms", [])
            if isinstance(forms_raw, list):
                alias_forms.extend(
                    str(f).strip() for f in forms_raw if isinstance(f, str) and str(f).strip()
                )

            phrases_raw = item.get("phrases", [])
            if isinstance(phrases_raw, list):
                for phrase_item in phrases_raw:
                    pattern = _parse_phrase_object(
                        phrase_item,
                        canonical=canonical,
                        category=category,
                        default_mute_span=default_mute_span,
                        exceptions=exceptions,
                    )
                    if pattern is not None:
                        phrases.append(pattern)

            expansions_raw = item.get("expansions", [])
            if isinstance(expansions_raw, list):
                for expansion in expansions_raw:
                    if not isinstance(expansion, str) or not expansion.strip():
                        continue
                    text = expansion.strip()
                    tokens = tokenize_phrase(text)
                    if len(tokens) < 2:
                        alias_forms.append(text)
                    else:
                        phrases.append(
                            PhrasePattern(
                                tokens=tokens,
                                canonical=canonical,
                                category=category,
                                mute_span=default_mute_span,
                                exceptions=exceptions,
                                raw=text,
                            )
                        )

            for surface in inflected_forms(canonical, plural=plural):
                alias_forms.append(surface)

            deduped_forms = tuple(
                dict.fromkeys(
                    form.strip()
                    for form in alias_forms
                    if form.strip() and form.strip().lower() != canonical.lower()
                )
            )

            entries.append(
                SwearEntry(
                    key=canonical,
                    category=category,
                    languages=languages,
                    plural=plural,
                    forms=deduped_forms,
                    phrases=tuple(phrases),
                    exceptions=exceptions,
                )
            )
    return entries


def collect_phrase_patterns(entries: list[SwearEntry]) -> tuple[PhrasePattern, ...]:
    """All phrase patterns, longest first to prefer specific matches."""
    patterns = [phrase for entry in entries for phrase in entry.phrases]
    return tuple(sorted(patterns, key=lambda p: len(p.tokens), reverse=True))


def word_aliases_from_entries(
    entries: list[SwearEntry],
) -> list[tuple[str, str, str, frozenset[str]]]:
    """Flatten to (alias, canonical, category, exceptions) for the word lexicon."""
    rows: list[tuple[str, str, str, frozenset[str]]] = []
    seen: set[tuple[str, str]] = set()

    def add(alias: str, canonical: str, category: str, exceptions: frozenset[str]) -> None:
        norm = normalize_token(alias)
        if len(norm) < 2:
            return
        dedupe = (norm, canonical)
        if dedupe in seen:
            return
        seen.add(dedupe)
        rows.append((alias, canonical, category, exceptions))

    for entry in entries:
        add(entry.key, entry.key, entry.category, entry.exceptions)
        for form in entry.forms:
            add(form, entry.key, entry.category, entry.exceptions)

    return rows
