"""Filmstrip disk cache touch / prune (no ffmpeg)."""

from __future__ import annotations

import time
from pathlib import Path

from corrcut.io.filmstrip_cache import (
    prune_filmstrip_cache,
    touch_filmstrip_cache,
)


def _fill_source(root: Path, key: str, *, nbytes: int, last_used: float) -> Path:
    folder = root / "filmstrip" / key
    folder.mkdir(parents=True)
    (folder / "tile.jpg").write_bytes(b"x" * nbytes)
    stamp = folder / ".last_used"
    stamp.write_text(f"{last_used:.3f}\n", encoding="utf-8")
    return folder


def test_touch_filmstrip_cache_writes_stamp(tmp_path: Path) -> None:
    cache = tmp_path / "filmstrip" / "abc"
    before = time.time()
    touch_filmstrip_cache(cache)
    stamp = cache / ".last_used"
    assert stamp.is_file()
    stamped = float(stamp.read_text(encoding="utf-8").strip())
    assert stamped >= before


def test_prune_removes_part_files_and_oldest_sources(tmp_path: Path) -> None:
    root = tmp_path
    _fill_source(root, "old", nbytes=1000, last_used=1.0)
    _fill_source(root, "mid", nbytes=1000, last_used=2.0)
    keep = _fill_source(root, "new", nbytes=1000, last_used=3.0)
    orphan = keep / "t00000000_128x72.0.part.jpg"
    orphan.write_bytes(b"partial")

    result = prune_filmstrip_cache(root, max_bytes=1500)

    assert result.removed_parts == 1
    assert not orphan.exists()
    assert not (root / "filmstrip" / "old").exists()
    assert (root / "filmstrip" / "new").is_dir()
    assert result.deleted_dirs >= 1
    assert result.total_bytes <= 1500


def test_prune_protects_open_source(tmp_path: Path) -> None:
    root = tmp_path
    _fill_source(root, "open", nbytes=2000, last_used=1.0)
    _fill_source(root, "other", nbytes=2000, last_used=2.0)

    result = prune_filmstrip_cache(root, max_bytes=1000, protect_keys={"open"})

    assert (root / "filmstrip" / "open").is_dir()
    assert not (root / "filmstrip" / "other").exists()
    assert result.deleted_dirs == 1
