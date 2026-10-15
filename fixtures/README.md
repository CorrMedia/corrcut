# Test media fixtures

Add short clips here for local development (gitignored by default):

| File | Purpose |
|------|---------|
| `stereo_short.mkv` | Stereo dialogue, full_mix strategy |
| `surround_51_short.mkv` | 5.1 with center channel, center_channel strategy |
| `ac3_surround_short.mkv` | Lossy surround (AC-3) for transcode-path testing (Phase 4) |

Generate a 2-second stereo test clip with FFmpeg:

```powershell
ffmpeg -f lavfi -i "sine=frequency=440:duration=2" -c:a flac fixtures/stereo_short.mkv
```

Probe it:

```powershell
python -m corrcut --probe fixtures/stereo_short.mkv
```
