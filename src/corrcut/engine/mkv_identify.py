"""mkvmerge identify helpers (subtitle/VobSub tooling — not MKV export)."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from corrcut.engine.hidden_process import no_window_kwargs
from corrcut.engine.toolchain import resolve_executable


def mkvmerge_identify(source_path: Path, *, mkvmerge: str | None = None) -> dict:
    exe = resolve_executable("mkvmerge") if mkvmerge is None else mkvmerge
    result = subprocess.run(
        [exe, "-J", str(source_path.resolve())],
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
        **no_window_kwargs(),
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr or result.stdout or "mkvmerge identify failed")
    return json.loads(result.stdout)
