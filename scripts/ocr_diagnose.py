"""Diagnose PGS bitmap subtitle OCR quality on a real media file.

Extracts the selected (or first) PGS subtitle stream, decodes the first N
subtitle events, saves each rendered image as a PNG, and prints the raw
PaddleOCR text alongside it. Use this to see exactly what OCR produces before
any cleanup/refinement runs.

Usage (from repo root, inside the .venv):

    python scripts/ocr_diagnose.py "C:\\path\\to\\movie.mkv"
    python scripts/ocr_diagnose.py "movie.mkv" --count 20 --stream 3 --lang en

Output images and a transcript.txt are written to ./ocr_diagnose_out/.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import tempfile
from pathlib import Path


def _probe_subtitle_streams(source: Path) -> list[dict]:
    import json

    proc = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-print_format",
            "json",
            "-show_streams",
            "-select_streams",
            "s",
            str(source),
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    data = json.loads(proc.stdout or "{}")
    streams: list[dict] = []
    for st in data.get("streams", []):
        streams.append(
            {
                "index": st.get("index"),
                "codec": st.get("codec_name", ""),
                "language": (st.get("tags", {}) or {}).get("language"),
            }
        )
    return streams


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("media", help="Path to the video file (mkv/mp4 with PGS subs)")
    parser.add_argument("--count", type=int, default=15, help="Number of events to sample")
    parser.add_argument("--stream", type=int, default=None, help="Subtitle stream index")
    parser.add_argument("--lang", default="en", help="PaddleOCR language code (default en)")
    parser.add_argument(
        "--out", default="ocr_diagnose_out", help="Output directory for PNGs + transcript"
    )
    args = parser.parse_args()

    source = Path(args.media).resolve()
    if not source.is_file():
        print(f"Media file not found: {source}", file=sys.stderr)
        return 2

    from corrcut.ai.subtitles import (
        extract_subtitle_sup,
        extract_subtitle_vobsub,
        is_dvd_subtitle_codec,
    )
    from corrcut.engine.subtitles.paddle_ocr_engine import ocr_rgba_image

    streams = _probe_subtitle_streams(source)
    print("Subtitle streams:")
    for s in streams:
        print(f"  index={s['index']} codec={s['codec']} language={s['language']}")

    bitmap = [
        s
        for s in streams
        if "pgs" in str(s["codec"]).lower()
        or is_dvd_subtitle_codec(str(s["codec"]))
    ]

    stream_index = args.stream
    codec = ""
    if stream_index is None:
        if not bitmap:
            print("No PGS/VobSub subtitle stream found. Pass --stream.", file=sys.stderr)
            return 2
        stream_index = bitmap[0]["index"]
        codec = str(bitmap[0]["codec"])
    else:
        codec = next((str(s["codec"]) for s in streams if s["index"] == stream_index), "")

    is_dvd = is_dvd_subtitle_codec(codec)
    print(f"\nUsing stream index {stream_index} (codec={codec}, dvd={is_dvd}), lang={args.lang}\n")

    out_dir = Path(args.out).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    transcript_lines: list[str] = []
    shown = 0

    with tempfile.TemporaryDirectory(prefix="ocr_diag_") as tmp:
        tmp_path = Path(tmp)
        if is_dvd:
            from corrcut.engine.subtitles.vobsub_decoder import decode_vobsub

            print("Extracting VobSub stream…")
            idx_path, _sub = extract_subtitle_vobsub(
                source,
                stream_index,
                tmp_path / "extracted",
                subtitle_streams=[s for s in streams],
            )
            events = decode_vobsub(idx_path)
            print(f"Decoded {len(events)} VobSub events.\n")
            for event in events:
                if shown >= args.count:
                    break
                png = out_dir / f"event{shown:03d}.png"
                try:
                    event.image.save(png)
                except Exception as exc:  # noqa: BLE001
                    print(f"  (could not save {png.name}: {exc})")
                text = ocr_rgba_image(event.image, lang=args.lang)
                line = f"[{shown:03d}] {event.start_ms / 1000.0:7.2f}s  {text.replace(chr(10), ' | ')}"
                print(line)
                transcript_lines.append(line)
                shown += 1
        else:
            from sup2srt.sup_decoder import decode_display_set
            from sup2srt.sup_parser import SupParser

            sup_path = tmp_path / "extracted.sup"
            print("Extracting PGS stream…")
            extract_subtitle_sup(source, stream_index, sup_path)
            display_sets = SupParser(sup_path).parse()
            print(f"Parsed {len(display_sets)} display sets.\n")
            for ds in display_sets:
                if shown >= args.count:
                    break
                pcs = getattr(ds, "pcs", None)
                if pcs is None or not getattr(pcs, "objects", []):
                    continue
                decoded = decode_display_set(ds)
                if not decoded:
                    continue
                sorted_imgs = sorted(decoded, key=lambda d: int(getattr(d, "y", 0)))
                parts = []
                for j, dec in enumerate(sorted_imgs):
                    img = getattr(dec, "image")
                    png = out_dir / f"event{shown:03d}_obj{j}.png"
                    try:
                        img.save(png)
                    except Exception as exc:  # noqa: BLE001
                        print(f"  (could not save {png.name}: {exc})")
                    text = ocr_rgba_image(img, lang=args.lang)
                    if text:
                        parts.append(text)
                joined = " / ".join(p.replace("\n", " | ") for p in parts)
                pts = float(getattr(pcs, "pts_ms", 0)) / 1000.0
                line = f"[{shown:03d}] {pts:7.2f}s  {joined}"
                print(line)
                transcript_lines.append(line)
                shown += 1

        (out_dir / "transcript.txt").write_text("\n".join(transcript_lines), encoding="utf-8")

    print(f"\nWrote {shown} events + images to {out_dir}")
    print(f"Transcript: {out_dir / 'transcript.txt'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
