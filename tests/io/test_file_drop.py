from pathlib import Path

from corrcut.io.file_drop import classify_open_path, first_openable_path


def test_classify_open_path_accepts_project_and_media() -> None:
    assert classify_open_path(Path("edit.corrcut")) == "project"
    assert classify_open_path(Path("EDIT.CORRCUT")) == "project"
    assert classify_open_path(Path("clip.mkv")) == "media"
    assert classify_open_path(Path("clip.MP4")) == "media"
    assert classify_open_path(Path("notes.txt")) is None


def test_first_openable_path_skips_unknown_files() -> None:
    chosen = first_openable_path(
        [Path("notes.txt"), Path("movie.mkv"), Path("edit.corrcut")]
    )
    assert chosen == Path("movie.mkv")
