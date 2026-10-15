"""Locate or fetch libmpv before python-mpv loads (Windows-first)."""

from __future__ import annotations

import importlib.util
import logging
import os
import shutil
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

from corrcut.config import AppPaths
from corrcut.engine.hidden_process import no_window_kwargs

logger = logging.getLogger(__name__)

_DLL_NAMES = ("libmpv-2.dll", "mpv-2.dll", "mpv-1.dll")

# Pinned mpv-winbuild dev archive (LGPL). Bump when updating preview stack.
_LIBMPV_WIN_TAG = "2026-10-02-3186d369f9"
_LIBMPV_WIN_ARCHIVE = "mpv-dev-lgpl-x86_64-20261002-git-3186d369f9.7z"
_7ZR_URL = "https://www.7-zip.org/a/7zr.exe"

_DOWNLOAD_ATTEMPTED = False
_PREPENDED_PATH_DIRS: set[str] = set()


def _package_root() -> Path:
    return Path(__file__).resolve().parent.parent


def _bundled_libmpv_dir() -> Path | None:
    for sub in ("resources/libmpv/win-amd64", "resources/libmpv/win_amd64"):
        candidate = _package_root() / sub
        if any((candidate / name).is_file() for name in _DLL_NAMES):
            return candidate
    return None


def _cache_libmpv_dir(paths: AppPaths) -> Path:
    return paths.cache_dir / "libmpv" / "win-amd64" / _LIBMPV_WIN_TAG


def _find_dll_in_dir(directory: Path) -> Path | None:
    for name in _DLL_NAMES:
        path = directory / name
        if path.is_file():
            return path
    return None


def _mpv_exe_directories() -> list[Path]:
    dirs: list[Path] = []
    mpv_exe = shutil.which("mpv")
    if mpv_exe:
        dirs.append(Path(mpv_exe).resolve().parent)
    for pattern in (
        Path(os.environ.get("PROGRAMFILES", r"C:\Program Files")) / "mpv",
        Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "mpv",
        Path.home() / "scoop" / "apps" / "mpv" / "current",
    ):
        if pattern.is_dir():
            dirs.append(pattern)
    return dirs


def libmpv_search_directories(paths: AppPaths) -> list[Path]:
    """Ordered directories that may contain libmpv (newest preference first)."""
    ordered: list[Path] = []
    seen: set[Path] = set()

    def add(path: Path | None) -> None:
        if path is None:
            return
        resolved = path.resolve()
        if resolved in seen or not resolved.is_dir():
            return
        seen.add(resolved)
        ordered.append(resolved)

    add(_bundled_libmpv_dir())
    cache_dir = _cache_libmpv_dir(paths)
    if _find_dll_in_dir(cache_dir) is not None:
        add(cache_dir)
    for directory in _mpv_exe_directories():
        add(directory)
    if sys.platform == "win32":
        add(Path(sys.executable).resolve().parent)
    return ordered


def _register_windows_dll_dir(directory: Path) -> None:
    """Register a directory for libmpv load (add_dll_directory + PATH for python-mpv)."""
    resolved = str(directory.resolve())
    if hasattr(os, "add_dll_directory"):
        os.add_dll_directory(resolved)
    if resolved not in _PREPENDED_PATH_DIRS:
        os.environ["PATH"] = resolved + os.pathsep + os.environ.get("PATH", "")
        _PREPENDED_PATH_DIRS.add(resolved)


def register_libmpv_dll_directories(paths: AppPaths) -> list[Path]:
    """Register DLL search paths (Windows). Returns dirs that contain a libmpv DLL."""
    hits: list[Path] = []
    for directory in libmpv_search_directories(paths):
        if _find_dll_in_dir(directory) is None:
            continue
        try:
            if sys.platform == "win32":
                _register_windows_dll_dir(directory)
            hits.append(directory)
            logger.debug("Registered libmpv DLL directory: %s", directory)
        except OSError as exc:
            logger.warning("Could not add DLL directory %s: %s", directory, exc)
    return hits


def can_import_mpv() -> bool:
    """True if python-mpv can load after register_libmpv_dll_directories."""
    if importlib.util.find_spec("mpv") is None:
        return False
    try:
        import mpv  # noqa: F401
    except OSError:
        return False
    return True


def _seven_zip_exe(paths: AppPaths) -> Path:
    """Return 7zr.exe (bundled next to app, on PATH, or cached under app data)."""
    bundled = _package_root() / "resources" / "tools" / "7zr.exe"
    if bundled.is_file():
        return bundled
    on_path = shutil.which("7zr") or shutil.which("7z")
    if on_path:
        return Path(on_path)
    for candidate in (
        Path(os.environ.get("PROGRAMFILES", r"C:\Program Files")) / "7-Zip" / "7z.exe",
        Path(os.environ.get("PROGRAMFILES(X86)", r"C:\Program Files (x86)")) / "7-Zip" / "7z.exe",
    ):
        if candidate.is_file():
            return candidate
    cached = paths.cache_dir / "tools" / "7zr.exe"
    if cached.is_file():
        return cached
    cached.parent.mkdir(parents=True, exist_ok=True)
    _download_archive(_7ZR_URL, cached)
    return cached


def _download_archive(url: str, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    logger.info("Downloading libmpv from %s", url)
    request = urllib.request.Request(url, headers={"User-Agent": "corrcut-libmpv-fetch"})
    with urllib.request.urlopen(request, timeout=120) as response:  # noqa: S310
        data = response.read()
    dest.write_bytes(data)


def _extract_dlls_from_7z(archive: Path, dest: Path, paths: AppPaths) -> None:
    dest.mkdir(parents=True, exist_ok=True)
    extractor = _seven_zip_exe(paths)
    result = subprocess.run(
        [str(extractor), "x", str(archive), f"-o{dest}", "-y", "*.dll"],
        capture_output=True,
        text=True,
        check=False,
        **no_window_kwargs(),
    )
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip()
        raise OSError(f"7-Zip failed to extract libmpv ({result.returncode}): {detail}")
    if _find_dll_in_dir(dest) is None:
        raise OSError(f"libmpv DLL not present after extracting {archive.name}")


def fetch_libmpv_to_cache(paths: AppPaths) -> Path:
    """Download and extract mpv dev build into the app cache (Windows)."""
    if sys.platform != "win32":
        raise OSError("Automatic libmpv download is only supported on Windows")

    dest = _cache_libmpv_dir(paths)
    ready = dest / ".libmpv-ready"
    existing = _find_dll_in_dir(dest)
    if existing is not None and ready.is_file():
        return dest

    url = (
        f"https://github.com/zhongfly/mpv-winbuild/releases/download/"
        f"{_LIBMPV_WIN_TAG}/{_LIBMPV_WIN_ARCHIVE}"
    )
    archive = paths.temp_dir / _LIBMPV_WIN_ARCHIVE
    try:
        if not archive.is_file() or archive.stat().st_size < 1024:
            _download_archive(url, archive)
        if dest.exists():
            shutil.rmtree(dest)
        _extract_dlls_from_7z(archive, dest, paths)
        ready.write_text(_LIBMPV_WIN_TAG, encoding="utf-8")
    except (urllib.error.URLError, OSError) as exc:
        raise OSError(f"Failed to obtain libmpv: {exc}") from exc
    finally:
        try:
            archive.unlink(missing_ok=True)
        except OSError:
            logger.debug("Could not remove temp archive %s", archive)

    found = _find_dll_in_dir(dest)
    if found is None:
        raise OSError("libmpv DLL missing after extract")
    logger.info("libmpv ready at %s (%s)", dest, found.name)
    return dest


def ensure_libmpv(paths: AppPaths) -> bool:
    """Find or fetch libmpv and register DLL directories. Does not import mpv."""
    global _DOWNLOAD_ATTEMPTED

    if register_libmpv_dll_directories(paths):
        return True
    if sys.platform != "win32":
        return False
    if _DOWNLOAD_ATTEMPTED:
        return False
    _DOWNLOAD_ATTEMPTED = True
    try:
        fetch_libmpv_to_cache(paths)
    except OSError as exc:
        logger.warning("%s", exc)
        return False
    return bool(register_libmpv_dll_directories(paths))


def prepare_libmpv(paths: AppPaths) -> bool:
    """Startup hook: resolve libmpv before python-mpv import (safe to call repeatedly)."""
    if register_libmpv_dll_directories(paths) and can_import_mpv():
        return True
    if ensure_libmpv(paths):
        return can_import_mpv()
    return False


def libmpv_status_message(paths: AppPaths) -> str:
    dll_ok = ensure_libmpv(paths)
    if dll_ok and can_import_mpv():
        return ""
    if dll_ok and importlib.util.find_spec("mpv") is None:
        return (
            "Video preview needs the python-mpv package. "
            'Install with: pip install -e ".[preview]" (or pip install python-mpv) and restart.'
        )
    if dll_ok:
        return (
            "Video preview found libmpv but python-mpv failed to load. "
            'Try: pip install -e ".[preview]" and restart.'
        )
    if sys.platform == "win32":
        return (
            "Video preview needs libmpv. Automatic download failed "
            '(check network). Install with: pip install -e ".[preview]" and restart.'
        )
    return (
        'Video preview needs libmpv. Install mpv on your system, then pip install -e ".[preview]".'
    )
