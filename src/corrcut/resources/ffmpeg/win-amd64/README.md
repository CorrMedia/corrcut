Place bundled FFmpeg binaries here (Windows x64).

Expected filenames:
- ffmpeg.exe
- ffprobe.exe

These files are intentionally not committed to git. Include them in your release
artifact/installer so end users do not need a separate FFmpeg install.

The installer downloads a pinned FFmpeg 8.1 GPL zip when this folder is empty.
The URL is `$FfmpegUrl` in `scripts/build-windows-release.ps1`. Update
`corrcut.config.MIN_FFMPEG_VERSION` / `MIN_FFPROBE_VERSION` when you
bump that pin.

