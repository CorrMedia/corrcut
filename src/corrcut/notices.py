"""Use notice and About text shown before publishing the app."""

from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

from corrcut.config import APP_NAME

_RESOURCES = Path(__file__).resolve().parent / "resources"


def use_notice() -> str:
    """Short statement of what the published app is for."""
    return (_RESOURCES / "USE.txt").read_text(encoding="utf-8").strip()


def known_limits() -> str:
    """First-release limits shown by the installer and shipped beside the app."""
    return (_RESOURCES / "LIMITS.txt").read_text(encoding="utf-8").strip()


def app_version() -> str:
    try:
        return version("corrcut")
    except PackageNotFoundError:
        return "0.1.0"


def third_party_notices_path() -> Path:
    return _RESOURCES / "THIRD_PARTY_NOTICES.txt"


def licenses_dir() -> Path:
    """Folder of full GPL/LGPL/Apache/Python license texts shipped with the app."""
    return _RESOURCES / "licenses"


def about_text() -> str:
    notices = third_party_notices_path().name
    return (
        f"{APP_NAME} {app_version()}\n\n"
        f"{use_notice()}\n\n"
        f"{APP_NAME} is MIT licensed. FFmpeg, libmpv, MKVToolNix, "
        "subtile-ocr, Qt/PySide6, and the other bundled tools keep their own "
        f"licenses. See LIMITS.txt, {notices}, and the licenses folder "
        f"({licenses_dir().name}/) next to the application."
    )
