"""Run vobsubocr (or compatible subtile-ocr) for DVD VobSub → text conversion."""

from __future__ import annotations

import os
import subprocess
import tempfile
from collections.abc import Callable
from pathlib import Path

from corrcut.ai.subtitles import SubtitleCue, parse_srt
from corrcut.engine.hidden_process import no_window_kwargs
from corrcut.engine.toolchain import bundled_executable, resolve_executable

# subtile-ocr is a maintained fork of elizagamedev/vobsubocr with the same CLI.
_VOBSUBOCR_COMMANDS = ("vobsubocr", "subtile-ocr")
_TESSERACT_BLACKLIST = "|\\/`~"


def vobsubocr_install_hint() -> str:
    return (
        "DVD (VobSub) subtitle OCR requires the bundled vobsubocr tool.\n\n"
        "Rebuild/bundle it with:\n"
        "  .\\scripts\\bundle-vobsubocr.ps1\n\n"
        "Or install subtile-ocr (vobsubocr-compatible CLI) on PATH:\n"
        "  cargo install subtile-ocr --locked"
    )


def resolve_vobsubocr() -> str | None:
    for command in _VOBSUBOCR_COMMANDS:
        path = bundled_executable(command)
        if path is None:
            path = resolve_executable(command)
        if path and _looks_like_vobsubocr(path):
            return path
    return None


def is_vobsubocr_available() -> bool:
    return resolve_vobsubocr() is not None


def _bundle_env(exe: str) -> dict[str, str]:
    env = os.environ.copy()
    bundle_dir = Path(exe).resolve().parent
    bundled_tessdata = bundle_dir / "tessdata"
    if bundled_tessdata.is_dir():
        env["TESSDATA_PREFIX"] = str(bundled_tessdata)
    path_entries = [str(bundle_dir)]
    existing = env.get("PATH", "")
    if existing:
        path_entries.append(existing)
    env["PATH"] = os.pathsep.join(path_entries)
    return env


def _looks_like_vobsubocr(path: str) -> bool:
    exe = Path(path)
    if not exe.is_file():
        return False
    try:
        result = subprocess.run(
            [path, "--help"],
            capture_output=True,
            text=True,
            timeout=20,
            check=False,
            **no_window_kwargs(),
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    output = f"{result.stdout}\n{result.stderr}".lower()
    return "vobsub" in output or "subtile" in output


def convert_vobsub_idx_to_cues(
    idx_path: Path,
    *,
    lang: str,
    progress: Callable[[int, str], None] | None = None,
    cancel_event: object | None = None,
) -> list[SubtitleCue]:
    """
    OCR a VobSub .idx (+ sibling .sub) pair via vobsubocr/subtile-ocr.

    Returns parsed subtitle cues. Raises RuntimeError when the tool is missing or fails.
    """
    from corrcut.engine.jobs.cancel import check_cancelled  # noqa: PLC0415

    exe = resolve_vobsubocr()
    if exe is None:
        raise RuntimeError(f"vobsubocr is not available. {vobsubocr_install_hint()}")

    idx_path = idx_path.resolve()
    sub_path = idx_path.with_suffix(".sub")
    if not idx_path.is_file():
        raise RuntimeError(f"VobSub index file not found: {idx_path}")
    if not sub_path.is_file():
        raise RuntimeError(f"VobSub data file not found: {sub_path}")

    if progress is not None:
        progress(15, f"Running vobsubocr ({lang})…")
    check_cancelled(cancel_event)  # type: ignore[arg-type]

    with tempfile.TemporaryDirectory(prefix="ms_vobsubocr_") as tmp:
        out_srt = Path(tmp) / "output.srt"
        cmd = [
            exe,
            "-l",
            lang,
            "-o",
            str(out_srt),
            "-c",
            f"tessedit_char_blacklist={_TESSERACT_BLACKLIST}",
            str(idx_path),
        ]
        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=3600,
                check=False,
                env=_bundle_env(exe),
                **no_window_kwargs(),
            )
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError("vobsubocr timed out") from exc
        except OSError as exc:
            raise RuntimeError(f"Could not run vobsubocr: {exc}") from exc

        check_cancelled(cancel_event)  # type: ignore[arg-type]

        # vobsubocr returns 1 when individual lines fail OCR but others succeed.
        if result.returncode not in (0, 1):
            detail = (result.stderr or result.stdout or "vobsubocr failed").strip()
            raise RuntimeError(detail)

        if not out_srt.is_file():
            detail = (result.stderr or result.stdout or "vobsubocr produced no SRT output").strip()
            raise RuntimeError(detail)

        content = out_srt.read_text(encoding="utf-8", errors="replace")
        cues = parse_srt(content)
        if not cues:
            detail = (result.stderr or result.stdout or "vobsubocr produced an empty SRT").strip()
            raise RuntimeError(
                "vobsubocr produced no subtitle lines. "
                f"Try another stream or install the matching Tesseract language pack ({lang!r}). "
                f"{detail}"
            )

        if progress is not None:
            progress(100, f"OCR complete ({len(cues)} cue(s))")
        return cues
