"""Entry point: GUI, toolchain check, or probe CLI."""

from __future__ import annotations

import argparse
from pathlib import Path

from corrcut.engine.hidden_process import install_hidden_subprocess

install_hidden_subprocess()


def _cmd_check_tools() -> int:
    from corrcut.engine.toolchain import check_toolchain

    result = check_toolchain()
    for tool in result.tools:
        status = "OK" if tool.ok else "FAIL"
        print(f"[{status}] {tool.name}: {tool.message}")
        if tool.executable:
            print(f"         {tool.executable}")
    return 0 if result.all_required_ok else 1


def _cmd_probe(path: Path) -> int:
    from corrcut.engine.probe import probe_media

    result = probe_media(path)
    for line in result.summary_lines():
        print(line)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="corrcut", description="CorrCUT")
    parser.add_argument(
        "--check-tools",
        action="store_true",
        help="Verify bundled ffmpeg/ffprobe/mkvmerge and exit",
    )
    parser.add_argument(
        "media_path",
        nargs="?",
        help="If set with --probe, run ffprobe summary and exit",
    )
    parser.add_argument(
        "--probe",
        action="store_true",
        help="Probe MEDIA_PATH and print stream info (no GUI)",
    )
    args = parser.parse_args(argv)

    if args.check_tools:
        return _cmd_check_tools()

    if args.probe:
        if not args.media_path:
            parser.error("--probe requires a media path")
        return _cmd_probe(Path(args.media_path))

    from corrcut.app import run_app

    return run_app()


if __name__ == "__main__":
    raise SystemExit(main())
