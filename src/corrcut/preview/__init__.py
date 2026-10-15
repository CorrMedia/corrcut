"""Live preview with FiltergraphCompiler — Phase 3+."""

from corrcut.preview.lavfi_mpv import compile_playback_lavfi, ffmpeg_graph_to_mpv_lavfi

__all__ = [
    "compile_playback_lavfi",
    "ffmpeg_graph_to_mpv_lavfi",
    "is_mpv_available",
]


def is_mpv_available() -> bool:
    from corrcut.preview.mpv_backend import is_mpv_available as _check

    return _check()
