"""OCR subtitle text cleanup heuristics."""

from fractions import Fraction

from corrcut.ai.subtitles import SubtitleCue
from corrcut.domain.subtitle_cue import SubtitleCue as ProjectSubtitleCue, SubtitleSource
from corrcut.domain.project import Project
from corrcut.domain.time_point import TimePoint
from corrcut.engine.subtitles.cue_sets import install_cue_set_for_tests
from corrcut.engine.subtitles.ocr_cleanup import clean_ocr_cues, clean_ocr_text
from corrcut.engine.subtitles.ocr_refine import reapply_ocr_cleanup, refine_ocr_text


def test_clean_ocr_text_strips_leading_slash() -> None:
    assert clean_ocr_text("/oser") == "oser"


def test_clean_ocr_text_strips_edge_junk_from_words() -> None:
    assert clean_ocr_text("|Hello /world~") == "- Hello world"


def test_clean_ocr_text_collapses_whitespace() -> None:
    assert clean_ocr_text("  What   the\theck  ") == "What the heck"


def test_clean_ocr_text_preserves_subtitle_tags() -> None:
    assert clean_ocr_text("[music]") == "[music]"
    assert clean_ocr_text("|/ [music]") == "[music]"


def test_clean_ocr_text_normalizes_ascii_music_markers() -> None:
    assert clean_ocr_text("#") == "\u266a"
    assert clean_ocr_text("# Hello there #") == "\u266a Hello there \u266a"
    assert clean_ocr_text("#Hello there#") == "\u266a Hello there \u266a"
    assert clean_ocr_text("##") == "\u266b"


def test_clean_ocr_text_normalizes_common_music_misreads() -> None:
    assert clean_ocr_text("&") == "\u266a"
    assert clean_ocr_text("& Sing along &") == "\u266a Sing along \u266a"
    assert clean_ocr_text('J"') == "\u266a"
    assert clean_ocr_text('J" Singing in the rain') == "\u266a Singing in the rain"


def test_clean_ocr_text_preserves_hash_in_words() -> None:
    assert clean_ocr_text("Play C# minor") == "Play C# minor"


def test_clean_ocr_text_normalizes_dialogue_dashes() -> None:
    assert clean_ocr_text("= Hello there") == "- Hello there"
    assert clean_ocr_text("=Hello there") == "- Hello there"
    assert clean_ocr_text("« Hello there") == "- Hello there"
    assert clean_ocr_text("| Hello there") == "- Hello there"


def test_clean_ocr_text_fixes_common_word_misreads() -> None:
    assert clean_ocr_text("Fm sorry about that") == "I'm sorry about that"
    assert clean_ocr_text("I knew aiso") == "I knew also"


def test_clean_ocr_text_splits_merged_speaker_lines() -> None:
    assert clean_ocr_text("= Hello = Goodbye") == "- Hello\n- Goodbye"
    assert clean_ocr_text("Hello = How are you?") == "- Hello\n- How are you?"
    assert clean_ocr_text("Hello « How are you?") == "- Hello\n- How are you?"


def test_clean_ocr_text_does_not_split_numeric_equals() -> None:
    assert clean_ocr_text("x = 5") == "x = 5"


def test_clean_ocr_text_cleans_multiline_cues() -> None:
    assert clean_ocr_text("/oser\n  Second line| ") == "oser\nSecond line"


def test_clean_ocr_cues_updates_text_and_drops_empty() -> None:
    cues = [
        SubtitleCue(
            start=Fraction(1),
            end=Fraction(2),
            text="/oser",
        ),
        SubtitleCue(
            start=Fraction(3),
            end=Fraction(4),
            text="|///",
        ),
        SubtitleCue(
            start=Fraction(5),
            end=Fraction(6),
            text="Keep me",
        ),
    ]

    cleaned = clean_ocr_cues(cues)

    assert len(cleaned) == 2
    assert cleaned[0].text == "oser"
    assert cleaned[1].text == "Keep me"
    assert cleaned[1] is cues[2]


def test_reapply_ocr_cleanup_updates_ocr_cue_set() -> None:
    cue = ProjectSubtitleCue.create(
        start=TimePoint(Fraction(1)),
        end=TimePoint(Fraction(2)),
        text="= Hello = Goodbye",
        source=SubtitleSource.OCR,
    )
    project = Project(selected_audio_stream_index=0)
    install_cue_set_for_tests(project, [cue], source=SubtitleSource.OCR)

    changed = reapply_ocr_cleanup(project)

    assert changed == 1
    assert project.subtitle_cues[0].text == "- Hello\n- Goodbye"
    assert project.subtitle_cues[0].edited_text == "- Hello\n- Goodbye"


def test_reapply_ocr_cleanup_updates_edited_text_from_display_source() -> None:
    cue = ProjectSubtitleCue.create(
        start=TimePoint(Fraction(1)),
        end=TimePoint(Fraction(2)),
        text="= Hello",
        edited_text="= Hello",
        source=SubtitleSource.OCR,
    )
    project = Project(selected_audio_stream_index=0)
    install_cue_set_for_tests(project, [cue], source=SubtitleSource.OCR)

    changed = reapply_ocr_cleanup(project)

    assert changed == 1
    assert project.subtitle_cues[0].text == "- Hello"
    assert project.subtitle_cues[0].edited_text == "- Hello"


def test_reapply_ocr_cleanup_skips_user_edited_display_text() -> None:
    cue = ProjectSubtitleCue.create(
        start=TimePoint(Fraction(1)),
        end=TimePoint(Fraction(2)),
        text="= Hello",
        edited_text="= Hello",
        source=SubtitleSource.OCR,
        user_edited=True,
    )
    project = Project(selected_audio_stream_index=0)
    install_cue_set_for_tests(project, [cue], source=SubtitleSource.OCR)

    changed = reapply_ocr_cleanup(project)

    assert changed == 1
    assert project.subtitle_cues[0].text == "- Hello"
    assert project.subtitle_cues[0].edited_text == "= Hello"


def test_reapply_ocr_cleanup_returns_zero_without_ocr_cues() -> None:
    project = Project(selected_audio_stream_index=0)
    assert reapply_ocr_cleanup(project) == 0
