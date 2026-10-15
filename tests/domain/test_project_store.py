"""Project file path helpers."""

from pathlib import Path

from corrcut.io.project_store import PROJECT_SUFFIX, default_project_path_for_source


def test_default_project_path_for_source() -> None:
    media = Path("C:/movies/clip.mkv")
    assert default_project_path_for_source(media) == Path("C:/movies/clip.corrcut")


def test_default_project_path_replaces_suffix() -> None:
    media = Path("/tmp/song.flac")
    assert default_project_path_for_source(media).suffix == PROJECT_SUFFIX
