"""Stop console tools from opening a window when CorrCUT is launched with pythonw."""

from __future__ import annotations

import subprocess
import sys

_installed = False


def no_window_kwargs(*, extra_creationflags: int = 0) -> dict[str, int]:
    """``creationflags`` for ``subprocess`` on Windows. Empty on other platforms."""
    if sys.platform != "win32":
        return {}
    return {"creationflags": subprocess.CREATE_NO_WINDOW | int(extra_creationflags)}


def install_hidden_subprocess() -> None:
    """Hide every later ``subprocess`` console on Windows, including library spawns.

    ``subprocess.run`` calls ``Popen`` through this module global, so replacing
    it covers both. Call this before importing libraries that may shell out.

    Must remain a real class: ``asyncio.windows_utils`` does
    ``class Popen(subprocess.Popen)``. Replacing ``Popen`` with a plain function
    raises ``TypeError: function() argument 'code' must be code, not str`` when
    faster-whisper / ctranslate2 import asyncio.
    """
    global _installed
    if _installed or sys.platform != "win32":
        return
    original = subprocess.Popen

    class HiddenPopen(original):  # type: ignore[misc, valid-type]
        def __init__(self, *args, **kwargs):  # type: ignore[no-untyped-def]
            flags = int(kwargs.get("creationflags") or 0)
            kwargs["creationflags"] = flags | subprocess.CREATE_NO_WINDOW
            super().__init__(*args, **kwargs)

    HiddenPopen.__name__ = "Popen"
    HiddenPopen.__qualname__ = "Popen"
    subprocess.Popen = HiddenPopen  # type: ignore[misc, assignment]
    _installed = True
