"""Map FiltergraphCompiler output to mpv lavfi-complex."""

from __future__ import annotations

import re

from corrcut.domain.operation import Operation, OperationType
from corrcut.domain.project import Project
from corrcut.engine.filtergraph import (
    CompileContext,
    CompiledFiltergraph,
    FiltergraphCompiler,
)
from corrcut.engine.video_filtergraph import compile_preview_video_effects
from corrcut.preview.playback_profile import PlaybackProfile

MPV_AUDIO_IN = "[aid]"
MPV_AUDIO_OUT = "[ao]"
MPV_VIDEO_IN = "[vid]"
MPV_VIDEO_OUT = "[vo]"
# Audio identity. Used as the audio half when a video graph is already present.
# Clearing lavfi-complex entirely deselects the audio track in mpv.
MPV_AUDIO_PASSTHROUGH = f"{MPV_AUDIO_IN}anull{MPV_AUDIO_OUT}"
# Video identity. lavfi-complex with only [ao] (no [vo]) blanks the picture after
# a video graph has been applied, so "Edited" off must keep [vo] wired.
MPV_VIDEO_PASSTHROUGH = f"{MPV_VIDEO_IN}null{MPV_VIDEO_OUT}"
MPV_PASSTHROUGH = f"{MPV_VIDEO_PASSTHROUGH};{MPV_AUDIO_PASSTHROUGH}"


def ffmpeg_graph_to_mpv_lavfi(graph: str, *, output_label: str = "aout") -> str:
    """Translate FFmpeg audio stream labels to mpv lavfi-complex labels."""
    converted = graph
    converted = re.sub(r"\[0:a(?::\d+)?\]", MPV_AUDIO_IN, converted)
    converted = re.sub(r"\[0:\d+\]", MPV_AUDIO_IN, converted)
    converted = converted.replace(f"[{output_label}]", MPV_AUDIO_OUT)
    return converted


def ffmpeg_video_graph_to_mpv_lavfi(graph: str, *, output_label: str = "vout") -> str:
    """Translate FFmpeg video stream labels to mpv lavfi-complex labels."""
    converted = graph.replace("[0:v]", MPV_VIDEO_IN)
    converted = converted.replace(f"[{output_label}]", MPV_VIDEO_OUT)
    return converted.rstrip(";")


def compile_preview_filtergraph(
    project: Project,
    *,
    operations: list[Operation] | None = None,
) -> CompiledFiltergraph | None:
    """Compile selected-track audio ops for preview (mute/volume/beep)."""
    if project.source is None:
        return None
    track = project.selected_track()
    if track is None:
        return None
    ops = project.operations_for_selected_track() if operations is None else operations
    ordinal = project.audio_ordinal_for_stream(track.stream_index)
    return FiltergraphCompiler().compile_for_audio_stream(
        ops,
        CompileContext(track=track, filter_time_origin_s=0.0, audition_preview_channels=None),
        audio_ordinal=ordinal,
    )


# Preview only builds video effect chains near the playhead. Crop/scale blur
# stages are not enable-gated in FFmpeg, so distant segments still cost every frame.
PREVIEW_VIDEO_WINDOW_RADIUS_S = 20.0


def compile_playback_lavfi(
    project: Project,
    *,
    edits_enabled: bool = True,
    apply_zoom: bool = True,
    playhead_s: float | None = None,
    video_window_radius_s: float = PREVIEW_VIDEO_WINDOW_RADIUS_S,
    profile: PlaybackProfile | None = None,
) -> str | None:
    """
    Build lavfi-complex for live preview.

    When edits_enabled is False, or *profile* selects nothing, returns identity
    filters for both video and audio so mpv keeps [vo] and [ao] wired to the
    original media. When edits are on: mute/volume/beep on the selected track,
    plus blank/cover/blur/pixelate/zoom/crop overlays, limited to operations the
    profile includes. Video effects always include audio (passthrough if needed).

    Zoom punch-in is omitted when apply_zoom is False so a selected box can be
    placed on the original frame. Preview passes True during playback, and while
    paused when no authoring box is on screen.

    When ``playhead_s`` is set, only video segments overlapping
    ``[playhead - radius, playhead + radius]`` are compiled.
    """
    if not edits_enabled:
        return MPV_PASSTHROUGH
    active = profile if profile is not None else PlaybackProfile.full()
    if active.is_passthrough():
        return MPV_PASSTHROUGH

    audio_mpv: str | None = None
    audio_ops = [op for op in project.operations_for_selected_track() if active.matches(op)]
    compiled = compile_preview_filtergraph(project, operations=audio_ops)
    if compiled is not None:
        audio_mpv = ffmpeg_graph_to_mpv_lavfi(
            compiled.filter_complex,
            output_label=compiled.output_audio_label,
        )

    video_ops = [
        op
        for op in project.timeline_operations()
        if op.is_item_level
        and (apply_zoom or op.type != OperationType.ZOOM)
        and active.matches(op)
    ]
    matte_box = None
    frame_w = 0
    frame_h = 0
    if project.preserve_letterbox_on_zoom and project.video_matte is not None:
        if not project.video_matte.is_trivial():
            matte_box = project.video_matte.as_box()
            if project.source is not None and isinstance(project.source.probe, dict):
                streams = project.source.probe.get("video_streams") or []
                if isinstance(streams, list) and streams and isinstance(streams[0], dict):
                    try:
                        frame_w = int(streams[0].get("width") or 0)
                        frame_h = int(streams[0].get("height") or 0)
                    except (TypeError, ValueError):
                        frame_w, frame_h = 0, 0
    time_window: tuple[float, float] | None = None
    if playhead_s is not None:
        radius = max(1.0, float(video_window_radius_s))
        center = max(0.0, float(playhead_s))
        time_window = (center - radius, center + radius)
    video_compiled = compile_preview_video_effects(
        video_ops,
        preserve_letterbox=bool(project.preserve_letterbox_on_zoom),
        matte=matte_box,
        frame_width=frame_w,
        frame_height=frame_h,
        time_window=time_window,
    )
    if video_compiled is None:
        # After any video lavfi graph, an audio-only complex blanks [vo].
        # Always keep a video identity pad when edits are enabled.
        if audio_mpv:
            return f"{MPV_VIDEO_PASSTHROUGH};{audio_mpv}"
        return MPV_PASSTHROUGH

    video_mpv = ffmpeg_video_graph_to_mpv_lavfi(
        video_compiled.filter_complex,
        output_label=video_compiled.output_video_label,
    )
    if not audio_mpv:
        audio_mpv = MPV_AUDIO_PASSTHROUGH
    return f"{video_mpv};{audio_mpv}"
