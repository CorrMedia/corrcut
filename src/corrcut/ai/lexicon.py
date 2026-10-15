"""Profanity lexicon matching with normalization and variants (Phase 5)."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from corrcut.ai.normalize import normalize_token
from corrcut.ai.phrase_match import is_blocked_by_exceptions
from corrcut.ai.swears_loader import (
    SwearEntry,
    load_swear_entries,
    word_aliases_from_entries,
)

# Variants shorter than this are not registered (avoids e.g. "an" from "anus" stems).
_MIN_VARIANT_LEN = 3

# Used only when ``swears/`` is missing (tests / misconfigured installs).
_FALLBACK_TERMS: tuple[str, ...] = (
    "ass",
    "asshole",
    "bitch",
    "cock",
    "crap",
    "cunt",
    "damn",
    "dick",
    "fuck",
    "shit",
    "whore",
)


def _simple_stems(word: str) -> set[str]:
    forms = {word}
    if word.endswith("ing") and len(word) > 5:
        forms.add(word[:-3])
        forms.add(word[:-3] + "e")
    if word.endswith("ed") and len(word) > 4:
        forms.add(word[:-2])
        forms.add(word[:-1])
    if word.endswith("s") and len(word) > 3:
        forms.add(word[:-1])
    return forms


def _variant_forms(base: str) -> set[str]:
    forms = _simple_stems(base)
    extra: set[str] = set()
    for form in list(forms):
        if "u" in form:
            stripped = form.replace("u", "")
            if len(stripped) >= _MIN_VARIANT_LEN:
                extra.add(stripped)
        if "ck" in form:
            extra.add(form.replace("ck", "k"))
    return forms | extra


@dataclass(frozen=True)
class LexiconMatch:
    term: str
    normalized: str
    strength: float
    category: str = "unknown"


class ProfanityLexicon:
    def __init__(
        self,
        terms: tuple[str, ...] | None = None,
        *,
        swear_entries: list[SwearEntry] | None = None,
    ) -> None:
        self._entries: dict[str, str] = {}
        self._categories: dict[str, str] = {}
        self._exceptions: dict[str, frozenset[str]] = {}

        if swear_entries is not None:
            self._load_from_swear_entries(swear_entries)
        elif terms is not None:
            self._load_plain_terms(terms)
        else:
            loaded = load_swear_entries()
            if loaded:
                self._load_from_swear_entries(loaded)
            else:
                self._load_plain_terms(_FALLBACK_TERMS)

    @classmethod
    def from_swears_dir(cls, swears_dir: str | Path) -> ProfanityLexicon:
        entries = load_swear_entries(Path(swears_dir))
        return cls(swear_entries=entries)

    def term_count(self) -> int:
        return len(self._entries)

    def categories(self) -> frozenset[str]:
        return frozenset(self._categories.values())

    def _load_plain_terms(self, terms: tuple[str, ...]) -> None:
        for base in terms:
            self._register_alias(base, base, "builtin", frozenset())

    def _load_from_swear_entries(self, entries: list[SwearEntry]) -> None:
        for alias, canonical, category, exceptions in word_aliases_from_entries(entries):
            self._register_alias(alias, canonical, category, exceptions)

    def _register_alias(
        self,
        alias: str,
        canonical: str,
        category: str,
        exceptions: frozenset[str],
    ) -> None:
        norm_alias = normalize_token(alias)
        norm_canonical = normalize_token(canonical)
        if not norm_alias or not norm_canonical:
            return

        if canonical not in self._exceptions and exceptions:
            self._exceptions[canonical] = exceptions

        for variant in _variant_forms(norm_alias):
            if len(variant) < _MIN_VARIANT_LEN and variant != norm_canonical:
                continue
            strength = 1.0 if variant == norm_canonical else 0.92
            existing = self._entries.get(variant)
            if existing is None or strength > 0.95:
                self._entries[variant] = canonical
                self._categories[variant] = category

    def match(
        self,
        token: str,
        *,
        word_norms: list[str] | None = None,
        word_index: int | None = None,
    ) -> LexiconMatch | None:
        norm = normalize_token(token)
        if not norm:
            return None

        canonical = self._entries.get(norm)
        strength = 1.0
        if canonical is None:
            return None

        if norm != normalize_token(canonical):
            strength = 0.92

        exceptions = self._exceptions.get(canonical, frozenset())
        if (
            word_norms is not None
            and word_index is not None
            and is_blocked_by_exceptions(word_norms, word_index, exceptions)
        ):
            return None

        category = self._categories.get(norm, self._categories.get(canonical, "unknown"))
        return LexiconMatch(
            term=canonical,
            normalized=norm,
            strength=strength,
            category=category,
        )


def build_default_lexicon() -> ProfanityLexicon:
    return ProfanityLexicon()


DEFAULT_LEXICON = build_default_lexicon()
