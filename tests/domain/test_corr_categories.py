"""CorrMedia category catalog — ids and resolve rules match the plugin."""

from corrcut.domain.corr_categories import (
    CATEGORY_IDS,
    OTHER_ID,
    categories_for_suggestion,
    censored_profanity_text,
    combo_entries,
    match_muted_word,
    resolve_category,
    resolve_categories,
)

# Distinct node ids from jellyfin-plugin-SkipMedia schema/categories.md.
_PLUGIN_CATALOG_IDS = frozenset(
    {
        "profanity",
        "blasphemy",
        "racial_slurs",
        "childish_language",
        "captions_profanity",
        "sexual_reference",
        "word_arse",
        "word_ass",
        "word_bastard",
        "word_bitch",
        "word_bloody",
        "word_bollocks",
        "word_damn",
        "word_dick",
        "word_douche",
        "word_fuck",
        "word_fu",
        "word_goddamn",
        "word_hell",
        "word_piss",
        "word_prick",
        "word_screw",
        "word_shit",
        "word_god_derogatory",
        "word_jesus_derogatory",
        "word_christ_derogatory",
        "word_n_word",
        "word_other_racial_slurs",
        "word_other_bigoted_slurs",
        "word_bs",
        "word_bum",
        "word_butt",
        "word_butthole",
        "word_crap",
        "word_dumb",
        "word_effin",
        "word_fart",
        "word_freakin",
        "word_frickin",
        "word_geez",
        "word_gosh",
        "word_idiot",
        "word_omg",
        "word_poop",
        "word_shut_up",
        "word_stupid",
        "word_caption_arse",
        "word_caption_ass",
        "word_caption_bastard",
        "word_caption_bitch",
        "word_caption_damn",
        "word_caption_fuck",
        "word_caption_hell",
        "word_caption_shit",
        "word_caption_other",
        "word_affair",
        "word_hot_sexy",
        "word_innuendo",
        "word_porno",
        "word_prostitute",
        "word_slut",
        "word_std",
        "word_whore",
        "sex_with_nudity",
        "sex_without_nudity",
        "sexual_assault",
        "implied_sex",
        "sexually_suggestive",
        "vulgar_gestures",
        "female_nudity",
        "male_nudity",
        "implied_nudity",
        "female_immodesty",
        "male_immodesty",
        "male_female_immodesty",
        "nude_art",
        "kissing_heterosexual_normal",
        "kissing_heterosexual_passionate",
        "kissing_homosexual_normal",
        "kissing_homosexual_passionate",
        "gore",
        "graphic",
        "non_graphic",
        "implied_violence",
        "disturbing_images",
        "objectionable_scary",
        "drugs_illegal",
        "drugs_legal",
        "drugs_implied",
        "medical_graphic",
        "medical_procedures",
        "life_events",
        "bodily_functions",
        "opening_credits",
        "closing_credits",
        "recap_outtakes",
        "other",
    }
)


def test_catalog_matches_plugin_schema_ids() -> None:
    assert CATEGORY_IDS == _PLUGIN_CATALOG_IDS


def test_resolve_maps_sidecar_tokens() -> None:
    assert resolve_category(None) == [OTHER_ID]
    assert resolve_category("") == [OTHER_ID]
    assert resolve_category("unknown_token") == [OTHER_ID]
    assert resolve_category("test") == [OTHER_ID]
    assert resolve_category("commercial") == [OTHER_ID]
    assert resolve_category("profanity") == ["profanity"]
    assert resolve_category("fuck") == ["word_fuck"]
    assert resolve_category("f_word") == ["word_fuck"]
    assert resolve_category("ass") == ["word_ass"]
    assert resolve_category("graphic_violence") == ["graphic"]
    assert resolve_category("kiss") == ["kissing_heterosexual_normal"]
    assert resolve_category("credits") == ["opening_credits"]
    assert resolve_category("n_word") == ["word_n_word"]
    assert resolve_category("nword") == ["word_n_word"]
    assert resolve_category("sex_nudity") == ["sex_with_nudity", "female_nudity"]


def test_resolve_violence_alias_expands() -> None:
    assert resolve_category("violence") == ["gore", "graphic", "non_graphic"]


def test_resolve_all_empty_is_other() -> None:
    assert resolve_categories(None) == [OTHER_ID]
    assert resolve_categories([]) == [OTHER_ID]
    assert resolve_categories(["", "  "]) == [OTHER_ID]


def test_unknown_word_prefix_is_other_not_passthrough() -> None:
    assert resolve_category("not_a_real_word") == [OTHER_ID]
    assert resolve_category("word_not_in_catalog") == [OTHER_ID]


def test_combo_entries_cover_parents_and_words() -> None:
    ids = [entry.id for entry in combo_entries() if entry is not None]
    assert "profanity" in ids
    assert "word_fuck" in ids
    assert "word_ass" in ids
    assert OTHER_ID in ids
    assert None in combo_entries()


def test_category_media_kind_splits_spoken_from_seen() -> None:
    from corrcut.domain.corr_categories import (
        MEDIA_AUDIO,
        MEDIA_BOTH,
        MEDIA_VISUAL,
        category_applies_to_media,
        category_media_kind,
        option_for_category_id,
    )

    fuck = option_for_category_id("word_fuck")
    captions = option_for_category_id("captions_profanity")
    caption_word = option_for_category_id("word_caption_fuck")
    sex_ref = option_for_category_id("sexual_reference")
    gore = option_for_category_id("gore")
    nudity = option_for_category_id("female_nudity")
    drugs = option_for_category_id("drugs_illegal")
    credits = option_for_category_id("opening_credits")
    other = option_for_category_id("other")
    assert fuck is not None and captions is not None and caption_word is not None
    assert sex_ref is not None and gore is not None and nudity is not None
    assert drugs is not None and credits is not None and other is not None
    assert category_media_kind(fuck) == MEDIA_AUDIO
    assert category_media_kind(sex_ref) == MEDIA_AUDIO
    assert category_media_kind(captions) == MEDIA_VISUAL
    assert category_media_kind(caption_word) == MEDIA_VISUAL
    assert category_media_kind(gore) == MEDIA_VISUAL
    assert category_media_kind(nudity) == MEDIA_VISUAL
    assert category_media_kind(credits) == MEDIA_VISUAL
    assert category_media_kind(drugs) == MEDIA_BOTH
    assert category_media_kind(other) == MEDIA_BOTH
    assert category_applies_to_media(fuck, audio=True)
    assert not category_applies_to_media(fuck, audio=False)
    assert category_applies_to_media(gore, audio=False)
    assert not category_applies_to_media(gore, audio=True)
    assert category_applies_to_media(other, audio=True)
    assert category_applies_to_media(other, audio=False)


def test_censored_profanity_text_uses_catalog_spelling() -> None:
    assert censored_profanity_text("fuck", category="profanity") == "f*ck"
    assert censored_profanity_text("fucking", matched_term="fuck", category="profanity") == "f*ck"
    assert (
        censored_profanity_text("son of a bitch", matched_term="bitch", category="profanity")
        == "son of a b*tch"
    )
    assert censored_profanity_text("hell") == "h*ll"
    assert censored_profanity_text("nigger", category="racial") == "n-word"
    assert censored_profanity_text("test") == "test"
    assert censored_profanity_text("idiot", category="childish") == "idiot"


def test_categories_for_suggestion_prefers_word_leaf() -> None:
    assert categories_for_suggestion(matched_term="fuck", category="profanity") == [
        "word_fuck"
    ]
    assert categories_for_suggestion(matched_term="asshole", category="profanity") == [
        "word_ass"
    ]
    assert categories_for_suggestion(matched_term="whore", category="sexual") == [
        "word_whore"
    ]
    assert categories_for_suggestion(matched_term="anatomy", category="sexual") == [
        "sexual_reference"
    ]
    assert categories_for_suggestion(matched_term="crap", category="childish") == [
        "word_crap"
    ]
    assert categories_for_suggestion(matched_term="goof", category="childish") == [
        "childish_language"
    ]
    assert categories_for_suggestion(matched_term="chink", category="racial") == [
        "word_other_racial_slurs"
    ]
    assert categories_for_suggestion() == [OTHER_ID]


def test_match_muted_word_compounds_and_inflections() -> None:
    assert match_muted_word("fucking", preferred_parent="profanity") == "word_fuck"
    assert match_muted_word("motherfucker", preferred_parent="profanity") == "word_fuck"
    assert match_muted_word("bitches", preferred_parent="profanity") == "word_bitch"
    assert match_muted_word("bullshit", preferred_parent="profanity") == "word_shit"
    assert match_muted_word("dumbass", preferred_parent="profanity") == "word_ass"
    assert match_muted_word("goddamn", preferred_parent="blasphemy") == "word_goddamn"
    assert match_muted_word("jesus", preferred_parent="blasphemy") == "word_jesus_derogatory"
    assert match_muted_word("god", preferred_parent="blasphemy") == "word_god_derogatory"
    assert match_muted_word("porn", preferred_parent="sexual_reference") == "word_porno"
    assert match_muted_word("hooker", preferred_parent="sexual_reference") == "word_prostitute"
    assert match_muted_word("sexy", preferred_parent="sexual_reference") == "word_hot_sexy"
    assert match_muted_word("nigger", preferred_parent="racial_slurs") == "word_n_word"
    assert match_muted_word("faggot", preferred_parent="racial_slurs") == (
        "word_other_bigoted_slurs"
    )
    assert match_muted_word("cock", preferred_parent="sexual_reference") is None
