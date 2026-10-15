from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

from corrcut.config import AppPaths
from corrcut.preview.libmpv_loader import (
    _LIBMPV_WIN_TAG,
    _find_dll_in_dir,
    can_import_mpv,
    fetch_libmpv_to_cache,
    libmpv_search_directories,
    prepare_libmpv,
    register_libmpv_dll_directories,
)


def test_find_dll_in_dir(tmp_path: Path) -> None:
    dll = tmp_path / "libmpv-2.dll"
    dll.write_bytes(b"")
    assert _find_dll_in_dir(tmp_path) == dll


def test_search_includes_bundled_and_cache(tmp_path: Path) -> None:
    paths = AppPaths(
        app_data=tmp_path,
        cache_dir=tmp_path / "cache",
        log_dir=tmp_path / "logs",
        temp_dir=tmp_path / "temp",
    )
    paths.ensure_dirs()
    cache = paths.cache_dir / "libmpv" / "win-amd64" / _LIBMPV_WIN_TAG
    cache.mkdir(parents=True, exist_ok=True)
    (cache / "libmpv-2.dll").write_bytes(b"")

    dirs = libmpv_search_directories(paths)
    assert any(d == cache.resolve() for d in dirs)


@pytest.mark.skipif(
    not (Path.home() / ".corrcut" / "cache" / "libmpv").exists(),
    reason="libmpv cache not present",
)
def test_prepare_allows_mpv_import() -> None:
    paths = AppPaths.default()
    assert prepare_libmpv(paths)
    assert can_import_mpv()


def test_register_skips_missing_dirs(tmp_path: Path) -> None:
    paths = AppPaths(
        app_data=tmp_path,
        cache_dir=tmp_path / "cache",
        log_dir=tmp_path / "logs",
        temp_dir=tmp_path / "temp",
    )
    empty = tmp_path / "empty"
    empty.mkdir()
    hits = register_libmpv_dll_directories(paths)
    assert empty not in hits


@pytest.mark.skipif(sys.platform != "win32", reason="Windows libmpv fetch")
@pytest.mark.skipif(
    os.environ.get("CORRCUT_FETCH_LIBMPV") != "1",
    reason="set CORRCUT_FETCH_LIBMPV=1 to run network fetch test",
)
def test_fetch_libmpv_to_cache_integration(tmp_path: Path) -> None:
    paths = AppPaths(
        app_data=tmp_path,
        cache_dir=tmp_path / "cache",
        log_dir=tmp_path / "logs",
        temp_dir=tmp_path / "temp",
    )
    paths.ensure_dirs()
    dest = fetch_libmpv_to_cache(paths)
    assert _find_dll_in_dir(dest) is not None
