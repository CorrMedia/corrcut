"""Tests for vobsubocr CLI integration."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from corrcut.engine.subtitles.vobsubocr_cli import convert_vobsub_idx_to_cues

_SAMPLE_SRT = """1
00:00:01,000 --> 00:00:04,000
Hello world

2
00:00:05,000 --> 00:00:08,000
Second line
"""


def test_convert_vobsub_idx_to_cues_parses_srt(tmp_path: Path) -> None:
    idx_path = tmp_path / "sample.idx"
    sub_path = tmp_path / "sample.sub"
    idx_path.write_text("# placeholder idx\n", encoding="utf-8")
    sub_path.write_bytes(b"\x00")

    def fake_run(cmd, **kwargs):  # type: ignore[no-untyped-def]
        out = Path(cmd[cmd.index("-o") + 1])
        out.write_text(_SAMPLE_SRT, encoding="utf-8")

        class Result:
            returncode = 0
            stdout = ""
            stderr = ""

        return Result()

    with patch("corrcut.engine.subtitles.vobsubocr_cli.resolve_vobsubocr", return_value="vobsubocr"):
        with patch("subprocess.run", side_effect=fake_run):
            cues = convert_vobsub_idx_to_cues(idx_path, lang="eng")

    assert len(cues) == 2
    assert cues[0].text == "Hello world"
    assert cues[1].text == "Second line"


def test_convert_vobsub_idx_requires_sub_file(tmp_path: Path) -> None:
    idx_path = tmp_path / "sample.idx"
    idx_path.write_text("# placeholder idx\n", encoding="utf-8")

    with patch("corrcut.engine.subtitles.vobsubocr_cli.resolve_vobsubocr", return_value="vobsubocr"):
        with pytest.raises(RuntimeError, match="VobSub data file not found"):
            convert_vobsub_idx_to_cues(idx_path, lang="eng")


def test_looks_like_vobsubocr_requires_vobsub_in_help(tmp_path: Path) -> None:
    from corrcut.engine.subtitles.vobsubocr_cli import _looks_like_vobsubocr

    fake = tmp_path / "not-ocr.exe"
    fake.write_bytes(b"")

    class Result:
        stdout = "Usage: some-other-tool [options]"
        stderr = ""

    with patch("subprocess.run", return_value=Result()):
        assert _looks_like_vobsubocr(str(fake)) is False

    class VobResult:
        stdout = "vobsubocr — OCR VobSub subtitles"
        stderr = ""

    with patch("subprocess.run", return_value=VobResult()):
        assert _looks_like_vobsubocr(str(fake)) is True
