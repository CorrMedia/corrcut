from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QMimeData, QPoint, Qt, QUrl
from PySide6.QtGui import QDragEnterEvent, QDropEvent

from corrcut.domain.project import Project


def _file_mime(path: Path) -> QMimeData:
    mime = QMimeData()
    mime.setUrls([QUrl.fromLocalFile(str(path))])
    return mime


def _drag_enter(mime: QMimeData) -> QDragEnterEvent:
    return QDragEnterEvent(
        QPoint(20, 20),
        Qt.DropAction.CopyAction,
        mime,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
    )


def _drop(mime: QMimeData) -> QDropEvent:
    return QDropEvent(
        QPoint(20, 20),
        Qt.DropAction.CopyAction,
        mime,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
    )


def test_empty_editor_drop_opens_media(qtbot, editor_window, tmp_path: Path) -> None:
    media = tmp_path / "clip.mkv"
    media.write_bytes(b"")
    opened: list[Path] = []
    editor_window._begin_media_open = opened.append  # type: ignore[method-assign]

    mime = _file_mime(media)
    enter = _drag_enter(mime)
    assert editor_window.eventFilter(editor_window, enter)
    assert enter.isAccepted()

    drop = _drop(mime)
    assert editor_window.eventFilter(editor_window, drop)
    assert opened == [media]


def test_empty_editor_drop_opens_project(qtbot, editor_window, tmp_path: Path) -> None:
    project = tmp_path / "edit.corrcut"
    project.write_text("{}", encoding="utf-8")
    opened: list[Path] = []
    editor_window.load_project_file = lambda path: opened.append(path) or True  # type: ignore[method-assign]

    mime = _file_mime(project)
    drop = _drop(mime)
    assert editor_window.eventFilter(editor_window._preview_panel, drop)
    assert opened == [project]


def test_loaded_media_ignores_file_drop(qtbot, editor_window, tmp_path: Path) -> None:
    media = tmp_path / "clip.mp4"
    media.write_bytes(b"")
    editor_window._project = Project()
    editor_window._project.source = type("Source", (), {"path": media})()
    opened: list[Path] = []
    editor_window._begin_media_open = opened.append  # type: ignore[method-assign]

    drop = _drop(_file_mime(media))
    assert not editor_window.eventFilter(editor_window, drop)
    assert opened == []


def test_unknown_drop_is_ignored(qtbot, editor_window, tmp_path: Path) -> None:
    notes = tmp_path / "notes.txt"
    notes.write_text("hello", encoding="utf-8")
    drop = _drop(_file_mime(notes))
    assert not editor_window.eventFilter(editor_window, drop)
