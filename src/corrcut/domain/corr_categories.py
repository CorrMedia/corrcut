"""CorrMedia category taxonomy (mirrors Jellyfin EditCategoryCatalog).

Source of truth: jellyfin-plugin-SkipMedia
``schema/categories.md`` and ``Jellyfin.Plugin.CorrMedia/Models/EditCategoryCatalog.cs``.

``edits[].categories`` is an array of these ids. Action (mute, cover, …) is separate.
A parent id tags the whole group (``profanity``); a ``word_*`` id tags one word.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

OTHER_ID = "other"


@dataclass(frozen=True)
class CategoryOption:
    id: str
    label: str
    group: str
    parent_id: str | None = None
    parent_label: str | None = None

    def combo_label(self) -> str:
        if self.parent_label:
            return f"{self.parent_label}: {self.label}"
        return f"{self.group}: {self.label}"


@dataclass(frozen=True)
class CategoryBranch:
    """One catalog parent and its word leaves, in one dashboard group."""

    option: CategoryOption
    children: tuple[CategoryOption, ...] = ()


@dataclass(frozen=True)
class _Node:
    id: str
    label: str
    children: tuple[_Node, ...] = ()


def _w(stem: str, label: str) -> _Node:
    return _Node(f"word_{stem}", label)


_PROFANITY_WORDS = (
    _w("arse", "*rse"),
    _w("ass", "*ss"),
    _w("bastard", "b*st*rd"),
    _w("bitch", "b*tch"),
    _w("bloody", "bl**dy"),
    _w("bollocks", "b*ll*cks"),
    _w("damn", "d*mn"),
    _w("dick", "d*ck"),
    _w("douche", "d**che"),
    _w("fuck", "f*ck"),
    _w("fu", "F.U."),
    _w("goddamn", "g*dd*mn"),
    _w("hell", "h*ll"),
    _w("piss", "p*ss"),
    _w("prick", "pr*ck"),
    _w("screw", "scr*w"),
    _w("shit", "sh*t"),
)

_BLASPHEMY_WORDS = (
    _w("goddamn", "g*dd*mn"),
    _w("god_derogatory", "God (derogatory)"),
    _w("jesus_derogatory", "Jesus (derogatory)"),
    _w("christ_derogatory", "Christ (derogatory)"),
)

_RACIAL_WORDS = (
    _w("n_word", "n-word"),
    _w("other_racial_slurs", "Other racial slurs"),
    _w("other_bigoted_slurs", "Other bigoted slurs"),
)

_CHILDISH_WORDS = (
    _w("bs", "BS"),
    _w("bum", "bum"),
    _w("butt", "butt"),
    _w("butthole", "b*tthole"),
    _w("crap", "cr*p"),
    _w("dumb", "dumb"),
    _w("effin", "effin'"),
    _w("fart", "fart"),
    _w("freakin", "freakin'"),
    _w("frickin", "frickin'"),
    _w("geez", "geez"),
    _w("gosh", "gosh"),
    _w("idiot", "idiot"),
    _w("omg", "OMG"),
    _w("poop", "poop"),
    _w("shut_up", "shut up"),
    _w("stupid", "stupid"),
)

_CAPTION_WORDS = (
    _w("caption_arse", "*rse"),
    _w("caption_ass", "*ss"),
    _w("caption_bastard", "b*st*rd"),
    _w("caption_bitch", "b*tch"),
    _w("caption_damn", "d*mn"),
    _w("caption_fuck", "f*ck"),
    _w("caption_hell", "h*ll"),
    _w("caption_shit", "sh*t"),
    _w("caption_other", "Other captioned profanity"),
)

_SEXUAL_REFERENCE_WORDS = (
    _w("affair", "affair / cheating"),
    _w("hot_sexy", "hot / sexy"),
    _w("innuendo", "sexual innuendo"),
    _w("porno", "p*rno / p*rnography"),
    _w("prostitute", "prostitute"),
    _w("slut", "sl*t"),
    _w("std", "STD references"),
    _w("whore", "wh*re"),
)

# Majors are groupings only; most major slugs are not themselves tags.
_MAJORS: tuple[tuple[str, tuple[_Node, ...]], ...] = (
    (
        "Language",
        (
            _Node("profanity", "Profanity", _PROFANITY_WORDS),
            _Node("blasphemy", "Blasphemy", _BLASPHEMY_WORDS),
            _Node("racial_slurs", "Racial & Bigoted Slurs", _RACIAL_WORDS),
            _Node("childish_language", "Childish Language", _CHILDISH_WORDS),
            _Node("captions_profanity", "Captions with Profanity", _CAPTION_WORDS),
            _Node("sexual_reference", "Sexual Reference", _SEXUAL_REFERENCE_WORDS),
        ),
    ),
    (
        "Sex",
        (
            _Node("sex_with_nudity", "Sex with Nudity"),
            _Node("sex_without_nudity", "Sex without Nudity"),
            _Node("sexual_assault", "Sexual Assault"),
            _Node("implied_sex", "Implied Sex"),
            _Node("sexually_suggestive", "Sexually Suggestive"),
            _Node("sexual_reference", "Sexual Reference", _SEXUAL_REFERENCE_WORDS),
            _Node("vulgar_gestures", "Vulgar Gestures"),
        ),
    ),
    (
        "Nudity & Immodesty",
        (
            _Node("sex_with_nudity", "Sex with Nudity"),
            _Node("female_nudity", "Female Nudity"),
            _Node("male_nudity", "Male Nudity"),
            _Node("implied_nudity", "Implied Nudity"),
            _Node("female_immodesty", "Female Immodesty"),
            _Node("male_immodesty", "Male Immodesty"),
            _Node("male_female_immodesty", "Male & Female Immodesty"),
            _Node("nude_art", "Nude Statues & Paintings"),
        ),
    ),
    (
        "Kissing",
        (
            _Node("kissing_heterosexual_normal", "Heterosexual Normal"),
            _Node("kissing_heterosexual_passionate", "Heterosexual Passionate"),
            _Node("kissing_homosexual_normal", "Homosexual Normal"),
            _Node("kissing_homosexual_passionate", "Homosexual Passionate"),
        ),
    ),
    (
        "Violence",
        (
            _Node("gore", "Gore"),
            _Node("graphic", "Graphic Violence"),
            _Node("non_graphic", "Non-graphic Violence"),
            _Node("implied_violence", "Implied Violence"),
            _Node("disturbing_images", "Disturbing Images"),
            _Node("objectionable_scary", "Objectionable, Disturbing, or Scary"),
        ),
    ),
    (
        "Drugs & Alcohol",
        (
            _Node("drugs_illegal", "Illegal Usage"),
            _Node("drugs_legal", "Legal Usage"),
            _Node("drugs_implied", "Implied Usage"),
        ),
    ),
    (
        "Medical & Body Process",
        (
            _Node("medical_graphic", "Medical Graphic"),
            _Node("medical_procedures", "Medical Procedures"),
            _Node("life_events", "Life Events"),
            _Node("bodily_functions", "Bodily Functions/Jokes"),
        ),
    ),
    (
        "Credits & Extras",
        (
            _Node("opening_credits", "Opening Credits"),
            _Node("closing_credits", "Closing Credits"),
            _Node("recap_outtakes", "Episode Recap/Outtakes"),
        ),
    ),
    ("Other", (_Node(OTHER_ID, "Other"),)),
)


def _flatten_options() -> tuple[CategoryOption, ...]:
    options: list[CategoryOption] = []
    for group, nodes in _MAJORS:
        for node in nodes:
            options.append(CategoryOption(node.id, node.label, group))
            for child in node.children:
                options.append(
                    CategoryOption(
                        child.id,
                        child.label,
                        group,
                        parent_id=node.id,
                        parent_label=node.label,
                    )
                )
    return tuple(options)


CATEGORY_OPTIONS: tuple[CategoryOption, ...] = _flatten_options()
CATEGORY_IDS: frozenset[str] = frozenset(option.id for option in CATEGORY_OPTIONS)
_OPTION_BY_ID: dict[str, CategoryOption] = {}
for _option in CATEGORY_OPTIONS:
    _OPTION_BY_ID.setdefault(_option.id, _option)


def _relationship_maps() -> tuple[dict[str, frozenset[str]], dict[str, frozenset[str]]]:
    """Child id → parent ids, and parent id → child ids, across every group."""
    parents: dict[str, set[str]] = {}
    children: dict[str, set[str]] = {}
    for _group, nodes in _MAJORS:
        for node in nodes:
            for child in node.children:
                parents.setdefault(child.id, set()).add(node.id)
                children.setdefault(node.id, set()).add(child.id)
    return (
        {key: frozenset(value) for key, value in parents.items()},
        {key: frozenset(value) for key, value in children.items()},
    )


_PARENTS_OF, _CHILDREN_OF = _relationship_maps()


def category_parent_ids(category_id: str) -> frozenset[str]:
    """Parents that list this id. A word can sit under more than one group."""
    return _PARENTS_OF.get(category_id, frozenset())


def category_child_ids(category_id: str) -> frozenset[str]:
    """Word leaves under this parent. Empty when the id is not a group."""
    return _CHILDREN_OF.get(category_id, frozenset())


def category_branches() -> tuple[tuple[str, tuple[CategoryBranch, ...]], ...]:
    """Dashboard groups. The same id can appear in more than one group."""
    groups: list[tuple[str, tuple[CategoryBranch, ...]]] = []
    for group, nodes in _MAJORS:
        branches: list[CategoryBranch] = []
        for node in nodes:
            parent = CategoryOption(node.id, node.label, group)
            kids = tuple(
                CategoryOption(
                    child.id,
                    child.label,
                    group,
                    parent_id=node.id,
                    parent_label=node.label,
                )
                for child in node.children
            )
            branches.append(CategoryBranch(parent, kids))
        groups.append((group, tuple(branches)))
    return tuple(groups)

MEDIA_AUDIO = "audio"
MEDIA_VISUAL = "visual"
MEDIA_BOTH = "both"

# Language is spoken (mute/beep). On-screen captions are visual. Sex/nudity/
# violence/credits are seen, not heard — except sexual-reference words.
_GROUP_MEDIA: dict[str, str] = {
    "Language": MEDIA_AUDIO,
    "Sex": MEDIA_VISUAL,
    "Nudity & Immodesty": MEDIA_VISUAL,
    "Kissing": MEDIA_VISUAL,
    "Violence": MEDIA_VISUAL,
    "Drugs & Alcohol": MEDIA_BOTH,
    "Medical & Body Process": MEDIA_VISUAL,
    "Credits & Extras": MEDIA_VISUAL,
    "Other": MEDIA_BOTH,
}


def option_for_category_id(cat_id: str) -> CategoryOption | None:
    return _OPTION_BY_ID.get(cat_id)


def category_media_kind(option: CategoryOption) -> str:
    """Whether this tag is typically applied to audio, visual, or either."""
    if option.id == OTHER_ID or option.id == "bodily_functions":
        return MEDIA_BOTH
    if (
        option.id == "captions_profanity"
        or option.parent_id == "captions_profanity"
        or option.id.startswith("word_caption_")
    ):
        return MEDIA_VISUAL
    if option.id == "sexual_reference" or option.parent_id == "sexual_reference":
        return MEDIA_AUDIO
    return _GROUP_MEDIA.get(option.group, MEDIA_BOTH)


def category_applies_to_media(option: CategoryOption, *, audio: bool) -> bool:
    """True when *option* belongs on an audio (or visual) edit."""
    kind = category_media_kind(option)
    if kind == MEDIA_BOTH:
        return True
    return kind == (MEDIA_AUDIO if audio else MEDIA_VISUAL)


# Sidecar aliases from EditCategoryCatalog.Resolve (prefer canonical ids on export).
_ALIASES: dict[str, tuple[str, ...]] = {
    "sex": ("sex_with_nudity",),
    "immodesty": ("female_immodesty",),
    "nudity": ("female_nudity",),
    "nude": ("female_nudity",),
    "sex_nudity": ("sex_with_nudity", "female_nudity"),
    "sexnudity": ("sex_with_nudity", "female_nudity"),
    "sex_nudity_immodesty": ("sex_with_nudity", "female_nudity"),
    "graphic_violence": ("graphic",),
    "nongraphic": ("non_graphic",),
    "violence": ("gore", "graphic", "non_graphic"),
    "profane": ("profanity",),
    "language": ("profanity",),
    "swear": ("profanity",),
    "racial": ("racial_slurs",),
    "slur": ("racial_slurs",),
    "slurs": ("racial_slurs",),
    "bigoted": ("racial_slurs",),
    "kiss": ("kissing_heterosexual_normal",),
    "kissing": ("kissing_heterosexual_normal",),
    "drugs": ("drugs_illegal",),
    "alcohol": ("drugs_illegal",),
    "credits": ("opening_credits",),
    "f_word": ("word_fuck",),
    "fword": ("word_fuck",),
    "fuck": ("word_fuck",),
    "n_word": ("word_n_word",),
    "nword": ("word_n_word",),
    "test": (OTHER_ID,),
}

# Lexicon file stems that are not plugin aliases.
_LEXICON_GROUP_ALIASES: dict[str, str] = {
    "sexual": "sexual_reference",
    "childish": "childish_language",
    "racial": "racial_slurs",
}

_LETTERS = re.compile(r"[^a-z]+")

# Near-misses for catalog leaves (swear-list keys that are not the dashboard stem).
_WORD_SYNONYMS: dict[str, str] = {
    "porn": "word_porno",
    "pornography": "word_porno",
    "hooker": "word_prostitute",
    "hookers": "word_prostitute",
    "biatch": "word_bitch",
    "shite": "word_shit",
    "arsehole": "word_arse",
    "frick": "word_frickin",
    "fricking": "word_frickin",
    "freaking": "word_freakin",
    "effing": "word_effin",
    "omfg": "word_omg",
    "cheating": "word_affair",
    "cheater": "word_affair",
    "cheat": "word_affair",
    "innuendo": "word_innuendo",
}

_BIGOTED_WORDS = frozenset(
    {
        "faggot",
        "fag",
        "faggots",
        "dyke",
        "dykes",
        "tranny",
        "trannies",
    }
)


@dataclass(frozen=True)
class _WordStem:
    stem: str
    catalog_id: str
    parent_id: str


def _letters(raw: str) -> str:
    return _LETTERS.sub("", raw.strip().lower())


def _parent_ids() -> frozenset[str]:
    return frozenset(node.id for _group, nodes in _MAJORS for node in nodes)


def _build_word_stems() -> tuple[_WordStem, ...]:
    stems: list[_WordStem] = []
    for _group, nodes in _MAJORS:
        for node in nodes:
            for child in node.children:
                cid = child.id
                if not cid.startswith("word_") or cid.startswith("word_caption_"):
                    continue
                if cid in {
                    "word_other_racial_slurs",
                    "word_other_bigoted_slurs",
                    "word_n_word",
                }:
                    continue
                raw = cid[len("word_") :]
                parent_id = node.id
                if raw.endswith("_derogatory"):
                    stems.append(_WordStem(_letters(raw[: -len("_derogatory")]), cid, parent_id))
                elif raw == "hot_sexy":
                    stems.append(_WordStem("sexy", cid, parent_id))
                    stems.append(_WordStem("hotsexy", cid, parent_id))
                else:
                    stems.append(_WordStem(_letters(raw), cid, parent_id))
    stems.sort(key=lambda item: len(item.stem), reverse=True)
    return tuple(stems)


_WORD_STEMS: tuple[_WordStem, ...] = _build_word_stems()
_PARENT_IDS: frozenset[str] = _parent_ids()


def _inflection_candidates(compact: str) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()

    def add(token: str) -> None:
        if token and token not in seen:
            seen.add(token)
            out.append(token)

    add(compact)
    if compact.endswith("ing") and len(compact) > 5:
        add(compact[:-3])
        add(compact[:-3] + "e")
    if compact.endswith("in") and len(compact) > 4:
        add(compact[:-2])
        add(compact + "g")
    if compact.endswith("ed") and len(compact) > 4:
        add(compact[:-2])
        add(compact[:-1])
    if compact.endswith("ers") and len(compact) > 6:
        add(compact[:-3])
    if compact.endswith("er") and len(compact) > 5:
        add(compact[:-2])
    if compact.endswith("ies") and len(compact) > 4:
        add(compact[:-3] + "y")
    if compact.endswith("es") and len(compact) > 4:
        add(compact[:-2])
    if compact.endswith("s") and len(compact) > 3 and not compact.endswith("ss"):
        add(compact[:-1])
    return out


def _preferred_parent(lexicon_category: str) -> str | None:
    if not lexicon_category.strip():
        return None
    stem = normalize_category_token(lexicon_category)
    if stem in _LEXICON_GROUP_ALIASES:
        return _LEXICON_GROUP_ALIASES[stem]
    if stem in _PARENT_IDS:
        return stem
    mapped = _ALIASES.get(stem)
    if mapped and len(mapped) == 1 and mapped[0] in _PARENT_IDS:
        return mapped[0]
    return None


def _match_stems(compact: str, stems: tuple[_WordStem, ...]) -> str | None:
    allowed_ids = {stem.catalog_id for stem in stems}
    candidates = _inflection_candidates(compact)
    for cand in candidates:
        for stem in stems:
            if cand == stem.stem:
                return stem.catalog_id
        synonym = _WORD_SYNONYMS.get(cand)
        if synonym and synonym in allowed_ids:
            return synonym
    for stem in stems:
        if len(stem.stem) < 3:
            continue
        for cand in candidates:
            if len(cand) <= len(stem.stem):
                continue
            if cand.startswith(stem.stem) or cand.endswith(stem.stem):
                return stem.catalog_id
            if len(stem.stem) >= 4 and stem.stem in cand:
                return stem.catalog_id
    return None


def _racial_leaf(compact: str) -> str:
    if compact.startswith("nigg"):
        return "word_n_word"
    stripped = compact[:-1] if compact.endswith("s") and len(compact) > 3 else compact
    if compact in _BIGOTED_WORDS or stripped in _BIGOTED_WORDS:
        return "word_other_bigoted_slurs"
    return "word_other_racial_slurs"


def match_muted_word(text: str, *, preferred_parent: str | None = None) -> str | None:
    """Map a muted word to a ``word_*`` catalog id when one is a good fit."""
    compact = _letters(text)
    if not compact:
        return None
    as_word = f"word_{compact}"
    if as_word in CATEGORY_IDS and not as_word.startswith("word_caption_"):
        return as_word

    if preferred_parent:
        scoped = tuple(stem for stem in _WORD_STEMS if stem.parent_id == preferred_parent)
        matched = _match_stems(compact, scoped)
        if matched:
            return matched
        if preferred_parent == "racial_slurs":
            return _racial_leaf(compact)

    matched = _match_stems(compact, _WORD_STEMS)
    if matched:
        return matched
    if compact.startswith("nigg"):
        return "word_n_word"
    return None


def categories_for_suggestion(
    *,
    matched_term: str = "",
    matched_word: str = "",
    category: str = "",
) -> list[str]:
    """Pick a catalog id for an accepted AI mute.

    Prefer a ``word_*`` leaf that fits the muted word (including compounds and
    inflections). Otherwise use the lexicon file's parent group.
    """
    parent = _preferred_parent(category)
    for text in (matched_word, matched_term):
        if not text.strip():
            continue
        leaf = match_muted_word(text, preferred_parent=parent)
        if leaf:
            return [leaf]
    if parent:
        return [parent]
    if category.strip():
        return resolve_category(category)
    return [OTHER_ID]


def censored_profanity_text(
    text: str,
    *,
    matched_term: str = "",
    category: str = "",
) -> str:
    """Swap in the catalog's censored spelling when this profanity has one.

    ``f*ck`` replaces ``fuck``; a phrase keeps its other words (``son of a b*tch``).
    Words with no separate censored spelling are returned unchanged.
    """
    spoken = text.strip()
    if not spoken:
        return text
    parent = _preferred_parent(category)
    leaf_id: str | None = None
    for candidate in (spoken, matched_term):
        if not candidate.strip():
            continue
        leaf_id = match_muted_word(candidate, preferred_parent=parent)
        if leaf_id:
            break
    if leaf_id is None:
        return text
    option = option_for_category_id(leaf_id)
    if option is None or option.parent_id is None:
        return text
    label = option.label.strip()
    if not label or label.casefold() == spoken.casefold():
        return text
    if " " not in spoken and "\n" not in spoken:
        return label
    term = matched_term.strip()
    if term:
        replaced, count = re.subn(
            rf"\b{re.escape(term)}\b",
            lambda _match: label,
            spoken,
            count=1,
            flags=re.IGNORECASE,
        )
        if count:
            return replaced
    return label


def combo_entries() -> tuple[CategoryOption | None, ...]:
    """Picker rows in dashboard order. ``None`` is a separator between majors."""
    entries: list[CategoryOption | None] = []
    last_group: str | None = None
    for option in CATEGORY_OPTIONS:
        if last_group is not None and option.group != last_group:
            entries.append(None)
        entries.append(option)
        last_group = option.group
    return tuple(entries)


def normalize_category_token(raw: str) -> str:
    return (
        raw.strip()
        .lower()
        .replace(" ", "_")
        .replace("/", "_")
        .replace("-", "_")
        .replace("&", "_")
        .replace("*", "_")
    )


def is_known_category_token(raw: str) -> bool:
    """True when *raw* is a catalog id, word leaf, or published alias."""
    key = normalize_category_token(raw)
    if not key:
        return False
    if key in CATEGORY_IDS or f"word_{key}" in CATEGORY_IDS:
        return True
    return key in _ALIASES or key in _LEXICON_GROUP_ALIASES


def resolve_category(raw: str | None) -> list[str]:
    """Map one sidecar token to catalog ids. Empty/unknown becomes ``other``."""
    if raw is None or not str(raw).strip():
        return [OTHER_ID]
    key = normalize_category_token(str(raw))
    if key in CATEGORY_IDS:
        return [key]
    as_word = f"word_{key}"
    if as_word in CATEGORY_IDS:
        return [as_word]
    mapped = _ALIASES.get(key)
    if mapped:
        return list(mapped)
    return [OTHER_ID]


def resolve_categories(categories: list[str] | None) -> list[str]:
    """Resolve every token. Empty tags become ``other``."""
    if not categories:
        return [OTHER_ID]
    seen: set[str] = set()
    out: list[str] = []
    for token in categories:
        if token is None or not str(token).strip():
            continue
        for cid in resolve_category(token):
            if cid not in seen:
                seen.add(cid)
                out.append(cid)
    return out or [OTHER_ID]
