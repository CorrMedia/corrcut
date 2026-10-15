"""Short preview render for auditioning edits (Phase 2; full mpv preview in Phase 3)."""

from __future__ import annotations

from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path

from corrcut.domain.channel_scope import FRONT_CHANNEL_NAMES
from corrcut.domain.dialogue_target import DialogueTargetStrategy
from corrcut.domain.project import Project
from corrcut.engine.filtergraph import (
    CompileContext,
    CompiledFiltergraph,
    FiltergraphCompiler,
)
from corrcut.engine.jobs.subprocess_job import JobSpec
from corrcut.engine.toolchain import resolve_executable

# Pre-roll before playhead in the rendered excerpt (must match editor playback offset).
AUDITION_PRE_ROLL_S = 0.5


def audition_seek_seconds(playhead_s: float, pre_roll_s: float = AUDITION_PRE_ROLL_S) -> float:
    """File position where the audition WAV begins (filter t=0)."""
    return max(0.0, playhead_s - pre_roll_s)


@dataclass(frozen=True)
class AuditionSpec:
    output_wav: Path
    start_seconds: Fraction
    duration_seconds: Fraction = Fraction(3, 1)


def _audition_preview_channels(track_channels: int, channel_layout: str | None) -> frozenset[str]:
    """
    Front channels to include in center-channel audition output.

    We always audition the full front bed (not only edited channels), so muting one
    channel does not make the entire audition output disappear.
    """
    from corrcut.domain.channel_layout import layout_channel_labels

    labels = layout_channel_labels(channel_layout, track_channels)
    front = [c for c in labels if c in FRONT_CHANNEL_NAMES]
    if front:
        return frozenset(front)
    return frozenset({"FC"})


def audition_job_spec(
    project: Project, spec: AuditionSpec, ffmpeg: str | None = None
) -> JobSpec | None:
    if project.source is None:
        return None
    track = project.selected_track()
    if track is None:
        return None

    ops = project.operations_for_selected_track()
    compiler = FiltergraphCompiler()
    input_label = f"0:{track.stream_index}"

    playhead_s = float(spec.start_seconds)
    seek_s = audition_seek_seconds(playhead_s)
    dur = float(spec.duration_seconds)
    media = str(project.source.path.resolve())
    out = str(spec.output_wav)

    preview_channels: frozenset[str] | None = None
    center = track.dialogue_target_strategy == DialogueTargetStrategy.CENTER_CHANNEL
    if center and ops:
        preview_channels = _audition_preview_channels(track.channels, track.channel_layout)

    compiled = compiler.compile(
        ops,
        CompileContext(
            track=track,
            input_label=input_label,
            output_label="aout",
            filter_time_origin_s=seek_s,
            audition_preview_channels=preview_channels,
        ),
    )

    # Match waveform (FC-only) when there are no edits.
    if center and f"anull[{compiled.output_audio_label}]" in compiled.filter_complex:
        compiled = CompiledFiltergraph(
            filter_complex=f"[{input_label}]pan=mono|c0=FC[{compiled.output_audio_label}]",
            output_audio_label=compiled.output_audio_label,
        )

    filter_complex = compiled.filter_complex

    ffmpeg_exe = resolve_executable("ffmpeg") if ffmpeg is None else ffmpeg
    cmd = [
        ffmpeg_exe,
        "-hide_banner",
        "-y",
        # Keep input-seek before -i so filter t starts at clip-relative 0.
        "-ss",
        str(seek_s),
        "-i",
        media,
        "-vn",
        "-t",
        str(dur + 1.0),
        "-filter_complex",
        filter_complex,
        "-map",
        f"[{compiled.output_audio_label}]",
        "-ac",
        "2",
        out,
    ]
    return JobSpec(command=cmd, label="Audition preview")
