"""SRT formatting for project subtitle cues."""

from __future__ import annotations

from fractions import Fraction
from typing import Literal

from corrcut.domain.subtitle_cue import SubtitleCue

TextField = Literal["text", "edited_text"]


def _format_srt_timestamp(seconds: Fraction) -> str:
    total_ms = int(seconds * 1000)
    hours, rem = divmod(total_ms, 3_600_000)
    minutes, rem = divmod(rem, 60_000)
    secs, ms = divmod(rem, 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{ms:03d}"


def format_srt(
    cues: list[SubtitleCue],
    *,
    field: TextField = "edited_text",
) -> str:
    blocks: list[str] = []
    for index, cue in enumerate(cues, start=1):
        line = cue.edited_text if field == "edited_text" else cue.text
        blocks.append(
            "\n".join(
                [
                    str(index),
                    (
                        f"{_format_srt_timestamp(cue.start.seconds)} --> "
                        f"{_format_srt_timestamp(cue.end.seconds)}"
                    ),
                    line,
                ]
            )
        )
    return "\n\n".join(blocks) + ("\n" if blocks else "")


def write_srt(
    cues: list[SubtitleCue],
    path,
    *,
    field: TextField = "edited_text",
) -> None:
    path.write_text(format_srt(cues, field=field), encoding="utf-8")
