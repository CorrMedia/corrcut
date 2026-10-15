"""Detect and verify ffmpeg, ffprobe, mkvmerge.

All three must be bundled (or set via CORRCUT_TOOLS_DIR). PATH is not used.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from corrcut.config import (
    MIN_FFMPEG_VERSION,
    MIN_FFPROBE_VERSION,
    MIN_MKVMERGE_VERSION,
    env_value,
)

_BUNDLED_ONLY = frozenset({"ffmpeg", "ffprobe", "mkvmerge"})


@dataclass(frozen=True)
class ToolInfo:
    name: str
    executable: str
    version: tuple[int, int, int] | None
    version_string: str
    ok: bool
    message: str


@dataclass(frozen=True)
class ToolCheckResult:
    tools: tuple[ToolInfo, ...]

    @property
    def all_required_ok(self) -> bool:
        """ffmpeg + ffprobe required for Phase 0–3; mkvmerge required from Phase 4."""
        required = ("ffmpeg", "ffprobe")
        by_name = {t.name: t for t in self.tools}
        return all(by_name.get(n, ToolInfo(n, "", None, "", False, "missing")).ok for n in required)

    @property
    def all_ok(self) -> bool:
        return all(t.ok for t in self.tools)


def _parse_version(text: str) -> tuple[int, int, int] | None:
    match = re.search(r"(\d+)\.(\d+)(?:\.(\d+))?", text)
    if not match:
        return None
    major, minor, patch = match.group(1), match.group(2), match.group(3)
    return (int(major), int(minor), int(patch or 0))


def _version_at_least(found: tuple[int, int, int] | None, minimum: tuple[int, int]) -> bool:
    if found is None:
        return False
    return (found[0], found[1]) >= minimum


def _run_version(executable: str, args: list[str]) -> str:
    from corrcut.engine.hidden_process import no_window_kwargs

    result = subprocess.run(
        [executable, *args],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
        **no_window_kwargs(),
    )
    return (result.stdout or "") + (result.stderr or "")


def _package_root() -> Path:
    # src/corrcut/engine/toolchain.py -> src/corrcut
    return Path(__file__).resolve().parent.parent


def _exe_name(command: str) -> str:
    return command + (".exe" if sys.platform == "win32" else "")


def _tools_override_dir() -> Path | None:
    override = env_value("TOOLS_DIR")
    if not override:
        return None
    p = Path(override).expanduser()
    return p if p.is_dir() else None


def _bundled_resource_dirs(command: str) -> tuple[str, ...]:
    if sys.platform == "win32":
        if command in ("ffmpeg", "ffprobe"):
            return ("resources/ffmpeg/win-amd64", "resources/ffmpeg/win_amd64")
        if command == "mkvmerge":
            return ("resources/mkvtoolnix/win-amd64", "resources/mkvtoolnix/win_amd64")
        if command == "mkvextract":
            return ("resources/mkvtoolnix/win-amd64", "resources/mkvtoolnix/win_amd64")
        if command in ("vobsubocr", "subtile-ocr"):
            return ("resources/vobsubocr/win-amd64", "resources/vobsubocr/win_amd64")
    elif sys.platform == "darwin":
        if command in ("ffmpeg", "ffprobe"):
            return ("resources/ffmpeg/macos", "resources/ffmpeg/darwin")
        if command == "mkvmerge":
            return ("resources/mkvtoolnix/macos", "resources/mkvtoolnix/darwin")
        if command == "mkvextract":
            return ("resources/mkvtoolnix/macos", "resources/mkvtoolnix/darwin")
        if command in ("vobsubocr", "subtile-ocr"):
            return ("resources/vobsubocr/macos", "resources/vobsubocr/darwin")
    else:
        if command in ("ffmpeg", "ffprobe"):
            return (
                "resources/ffmpeg/linux-amd64",
                "resources/ffmpeg/linux_x86_64",
                "resources/ffmpeg/linux",
            )
        if command == "mkvmerge":
            return (
                "resources/mkvtoolnix/linux-amd64",
                "resources/mkvtoolnix/linux_x86_64",
                "resources/mkvtoolnix/linux",
            )
        if command == "mkvextract":
            return (
                "resources/mkvtoolnix/linux-amd64",
                "resources/mkvtoolnix/linux_x86_64",
                "resources/mkvtoolnix/linux",
            )
        if command in ("vobsubocr", "subtile-ocr"):
            return (
                "resources/vobsubocr/linux-amd64",
                "resources/vobsubocr/linux_x86_64",
                "resources/vobsubocr/linux",
            )
    return ()


def bundled_executable(command: str) -> str | None:
    """Return a bundled executable path if present, else None."""
    exe = _exe_name(command)
    override = _tools_override_dir()
    if override is not None:
        path = override / exe
        if path.is_file():
            return str(path)

    root = _package_root()
    for rel in _bundled_resource_dirs(command):
        path = root / rel / exe
        if path.is_file():
            return str(path)
    return None


def resolve_executable(command: str) -> str:
    """
    Resolve tool executable path.

    Preference order:
    - CORRCUT_TOOLS_DIR or bundled resources directory (when present)
    - PATH lookup (only for tools not in _BUNDLED_ONLY)
    - Fallback to command (lets subprocess raise a useful error)
    """
    resolved = bundled_executable(command)
    if resolved:
        return resolved
    if command in _BUNDLED_ONLY:
        return command
    return shutil.which(command) or command


def _check_tool(
    name: str,
    command: str,
    version_args: list[str],
    min_version: tuple[int, int],
    *,
    allow_path: bool = True,
) -> ToolInfo:
    bundled = bundled_executable(command)
    path = bundled or (shutil.which(command) if allow_path else None)
    if not path:
        return ToolInfo(
            name=name,
            executable="",
            version=None,
            version_string="",
            ok=False,
            message=(
                f"{command} not found (no bundled copy)"
                if not allow_path
                else f"{command} not found (no bundled copy; not on PATH)"
            ),
        )

    try:
        output = _run_version(path, version_args)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return ToolInfo(
            name=name,
            executable=path,
            version=None,
            version_string="",
            ok=False,
            message=str(exc),
        )

    version = _parse_version(output)
    version_string = output.strip().splitlines()[0] if output.strip() else "unknown"
    ok = _version_at_least(version, min_version)
    location = "bundled" if bundled else ("PATH" if allow_path else "missing")
    message = (
        f"ok ({location})"
        if ok
        else f"requires >={min_version[0]}.{min_version[1]}, got {version_string} ({location})"
    )

    return ToolInfo(
        name=name,
        executable=path,
        version=version,
        version_string=version_string,
        ok=ok,
        message=message,
    )


def check_toolchain() -> ToolCheckResult:
    tools = (
        _check_tool("ffmpeg", "ffmpeg", ["-version"], MIN_FFMPEG_VERSION, allow_path=False),
        _check_tool("ffprobe", "ffprobe", ["-version"], MIN_FFPROBE_VERSION, allow_path=False),
        _check_tool("mkvmerge", "mkvmerge", ["--version"], MIN_MKVMERGE_VERSION, allow_path=False),
    )
    return ToolCheckResult(tools=tools)
