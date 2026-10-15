import subprocess
import sys

import corrcut.engine.hidden_process as hidden_process
from corrcut.engine.hidden_process import no_window_kwargs


def test_no_window_kwargs_hide_console_on_windows() -> None:
    kwargs = no_window_kwargs()
    if sys.platform == "win32":
        assert kwargs["creationflags"] & subprocess.CREATE_NO_WINDOW
    else:
        assert kwargs == {}


def test_no_window_kwargs_keep_extra_flags() -> None:
    kwargs = no_window_kwargs(extra_creationflags=subprocess.IDLE_PRIORITY_CLASS)
    if sys.platform != "win32":
        assert kwargs == {}
        return
    flags = kwargs["creationflags"]
    assert flags & subprocess.CREATE_NO_WINDOW
    assert flags & subprocess.IDLE_PRIORITY_CLASS


def test_install_hidden_subprocess_covers_later_popen() -> None:
    original = subprocess.Popen
    seen: dict[str, int] = {}

    class _Recorder:
        def __init__(self, *args: object, **kwargs: object) -> None:
            del args
            seen["flags"] = int(kwargs.get("creationflags") or 0)

    subprocess.Popen = _Recorder  # type: ignore[misc, assignment]
    hidden_process._installed = False
    try:
        hidden_process.install_hidden_subprocess()
        subprocess.Popen(["ffmpeg"], creationflags=subprocess.IDLE_PRIORITY_CLASS)
    finally:
        subprocess.Popen = original
        hidden_process._installed = False

    if sys.platform != "win32":
        assert seen["flags"] == subprocess.IDLE_PRIORITY_CLASS
        return
    flags = seen["flags"]
    assert flags & subprocess.CREATE_NO_WINDOW
    assert flags & subprocess.IDLE_PRIORITY_CLASS


def test_install_hidden_subprocess_keeps_popen_subclassable() -> None:
    """asyncio.windows_utils subclasses subprocess.Popen on import."""
    original = subprocess.Popen
    hidden_process._installed = False
    try:
        hidden_process.install_hidden_subprocess()
        if sys.platform != "win32":
            assert subprocess.Popen is original
            return
        assert isinstance(subprocess.Popen, type)

        class _Child(subprocess.Popen):  # type: ignore[misc, valid-type]
            pass

        assert issubclass(_Child, subprocess.Popen)
    finally:
        subprocess.Popen = original
        hidden_process._installed = False


def test_install_hidden_subprocess_allows_asyncio_windows_utils_import() -> None:
    """Regression: bare-function Popen patch broke ctranslate2 → asyncio import."""
    if sys.platform != "win32":
        return
    original = subprocess.Popen
    hidden_process._installed = False
    # Force a fresh load of the module that subclasses subprocess.Popen.
    sys.modules.pop("asyncio.windows_utils", None)
    try:
        hidden_process.install_hidden_subprocess()
        import asyncio.windows_utils as windows_utils

        assert issubclass(windows_utils.Popen, subprocess.Popen)
    finally:
        subprocess.Popen = original
        hidden_process._installed = False
        sys.modules.pop("asyncio.windows_utils", None)
