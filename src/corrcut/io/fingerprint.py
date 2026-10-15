"""Cheap source file fingerprint (blueprint Section 7)."""

from __future__ import annotations

import hashlib
from pathlib import Path

CHUNK_SIZE = 1024 * 1024
EDGE_BYTES = 1024 * 1024


def fingerprint_file(path: Path) -> dict[str, object]:
    stat = path.stat()
    size = stat.st_size
    head_hash = _hash_range(path, 0, min(EDGE_BYTES, size))
    tail_hash = ""
    if size > EDGE_BYTES:
        tail_start = max(0, size - EDGE_BYTES)
        tail_hash = _hash_range(path, tail_start, size)
    return {
        "size": size,
        "head_sha256": head_hash,
        "tail_sha256": tail_hash,
    }


def _hash_range(path: Path, start: int, end: int) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        handle.seek(start)
        remaining = end - start
        while remaining > 0:
            chunk = handle.read(min(CHUNK_SIZE, remaining))
            if not chunk:
                break
            digest.update(chunk)
            remaining -= len(chunk)
    return digest.hexdigest()
