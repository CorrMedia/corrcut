"""Load swear JSON lists from swears/."""

from corrcut.ai.lexicon import ProfanityLexicon
from corrcut.ai.normalize import normalize_token
from corrcut.ai.swears_loader import (
    collect_phrase_patterns,
    load_swear_entries,
    repo_swears_dir,
    resolve_swears_dir,
)


def test_repo_swears_dir_exists() -> None:
    assert repo_swears_dir().is_dir()


def test_load_swear_entries_includes_categories() -> None:
    entries = load_swear_entries(repo_swears_dir())
    categories = {e.category for e in entries}
    assert "profanity" in categories
    assert "sexual" in categories
    assert any(e.key == "fuck" for e in entries)


def test_lexicon_from_swears_dir_has_many_terms() -> None:
    lex = ProfanityLexicon.from_swears_dir(repo_swears_dir())
    assert lex.term_count() > 150
    assert "sexual" in lex.categories()


def test_expansion_shitty_maps_to_shit() -> None:
    lex = ProfanityLexicon.from_swears_dir(repo_swears_dir())
    match = lex.match("shitty")
    assert match is not None
    assert match.term == "shit"


def test_expansion_shitting_maps_to_shit() -> None:
    lex = ProfanityLexicon.from_swears_dir(repo_swears_dir())
    match = lex.match("shitting")
    assert match is not None
    assert match.term == "shit"


def test_expansion_bitching_maps_to_bitch() -> None:
    lex = ProfanityLexicon.from_swears_dir(repo_swears_dir())
    match = lex.match("bitching")
    assert match is not None
    assert match.term == "bitch"


def test_expansion_bitchin_maps_to_bitch() -> None:
    lex = ProfanityLexicon.from_swears_dir(repo_swears_dir())
    match = lex.match("bitchin")
    assert match is not None
    assert match.term == "bitch"


def test_expansion_bullshitting_maps_to_bullshit() -> None:
    lex = ProfanityLexicon.from_swears_dir(repo_swears_dir())
    match = lex.match("bullshitting")
    assert match is not None
    assert match.term == "bullshit"


def test_exception_blocks_token_sequence() -> None:
    lex = ProfanityLexicon.from_swears_dir(repo_swears_dir())
    norms = [normalize_token(w) for w in ["he", "hit", "the", "golf", "balls"]]
    idx = norms.index("balls")
    assert lex.match("balls", word_norms=norms, word_index=idx) is None


def test_exception_does_not_block_unrelated_phrase() -> None:
    lex = ProfanityLexicon.from_swears_dir(repo_swears_dir())
    norms = [normalize_token(w) for w in ["you", "have", "big", "balls"]]
    idx = norms.index("balls")
    match = lex.match("balls", word_norms=norms, word_index=idx)
    assert match is not None
    assert match.term == "balls"


def test_multiword_expansions_become_phrases_not_aliases() -> None:
    entries = load_swear_entries(repo_swears_dir())
    phrases = collect_phrase_patterns(entries)
    assert any(p.raw == "fuck you" for p in phrases)
    lex = ProfanityLexicon.from_swears_dir(repo_swears_dir())
    assert lex.match("you") is None


def test_resolve_swears_dir_from_repo() -> None:
    path = resolve_swears_dir()
    assert path is not None
    assert path.name == "swears"
    assert (path / "profanity.json").is_file()
