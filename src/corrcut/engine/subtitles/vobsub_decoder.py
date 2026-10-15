"""Decode DVD VobSub (.idx/.sub) subtitle bitmaps into RGBA images.

VobSub stores subtitles as SPU (Sub-Picture Unit) packets inside an MPEG-2
Program Stream (.sub), indexed by an .idx text file that lists a start
timestamp and byte offset per event. Each SPU carries a 2-bit-per-pixel,
RLE-compressed, interlaced bitmap plus a control sequence (palette, alpha,
display coordinates, timing).

Decoding the bitmaps ourselves lets us run a modern OCR engine (PaddleOCR)
instead of the bundled Tesseract-based ``vobsubocr`` CLI, which produces much
poorer text on stylized DVD captions.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from PIL import Image

_PACK_START = b"\x00\x00\x01\xba"
_PES_PRIVATE1 = 0xBD

_TS_RE = re.compile(
    r"timestamp:\s*(\d+):(\d+):(\d+):(\d+),\s*filepos:\s*([0-9A-Fa-f]+)"
)
_SIZE_RE = re.compile(r"size:\s*(\d+)\s*x\s*(\d+)")
_PALETTE_RE = re.compile(r"palette:\s*(.+)")


@dataclass
class VobSubEvent:
    start_ms: float
    end_ms: float | None
    image: Image.Image


@dataclass
class _IdxInfo:
    width: int
    height: int
    palette: list[tuple[int, int, int]]
    entries: list[tuple[float, int]]  # (start_ms, filepos)
    stream_id: int  # 0x20 + langidx


def parse_idx(idx_path: Path) -> _IdxInfo:
    text = idx_path.read_text(encoding="utf-8", errors="replace")
    width, height = 720, 480
    palette: list[tuple[int, int, int]] = []
    entries: list[tuple[float, int]] = []
    langidx = 0

    for line in text.splitlines():
        stripped = line.strip()
        size_match = _SIZE_RE.search(stripped)
        if size_match:
            width = int(size_match.group(1))
            height = int(size_match.group(2))
            continue
        if stripped.startswith("langidx:"):
            try:
                langidx = int(stripped.split(":", 1)[1].strip())
            except ValueError:
                langidx = 0
            continue
        pal_match = _PALETTE_RE.match(stripped)
        if pal_match:
            palette = _parse_palette(pal_match.group(1))
            continue
        ts_match = _TS_RE.search(stripped)
        if ts_match:
            hh, mm, ss, ms = (int(ts_match.group(i)) for i in range(1, 5))
            start_ms = ((hh * 60 + mm) * 60 + ss) * 1000 + ms
            filepos = int(ts_match.group(5), 16)
            entries.append((float(start_ms), filepos))

    if not palette:
        palette = [(0, 0, 0)] + [(255, 255, 255)] * 15
    return _IdxInfo(
        width=width,
        height=height,
        palette=palette,
        entries=entries,
        stream_id=0x20 + max(0, langidx),
    )


def _parse_palette(raw: str) -> list[tuple[int, int, int]]:
    colors: list[tuple[int, int, int]] = []
    for token in raw.split(","):
        token = token.strip()
        if len(token) != 6:
            continue
        try:
            r = int(token[0:2], 16)
            g = int(token[2:4], 16)
            b = int(token[4:6], 16)
        except ValueError:
            continue
        colors.append((r, g, b))
    while len(colors) < 16:
        colors.append((255, 255, 255))
    return colors


def decode_vobsub(idx_path: Path) -> list[VobSubEvent]:
    """Decode all subtitle events from an .idx/.sub pair into RGBA images."""
    idx_path = idx_path.resolve()
    sub_path = idx_path.with_suffix(".sub")
    if not sub_path.is_file():
        raise FileNotFoundError(f"VobSub data file not found: {sub_path}")

    info = parse_idx(idx_path)
    sub_data = sub_path.read_bytes()

    events: list[VobSubEvent] = []
    entries = info.entries
    for i, (start_ms, filepos) in enumerate(entries):
        next_pos = entries[i + 1][1] if i + 1 < len(entries) else len(sub_data)
        spu = _read_spu(sub_data, filepos, next_pos, info.stream_id)
        if spu is None:
            # mkvextract langidx often does not match the PES substream id.
            spu = _read_spu(sub_data, filepos, next_pos, None)
        if spu is None:
            continue
        decoded = _decode_spu(spu, info)
        if decoded is None:
            continue
        image, duration_ms = decoded
        end_ms = start_ms + duration_ms if duration_ms else None
        events.append(VobSubEvent(start_ms=start_ms, end_ms=end_ms, image=image))
    return events


def _read_spu(data: bytes, start: int, limit: int, stream_id: int | None) -> bytes | None:
    """Accumulate one SPU payload from private_stream_1 PES packets.

    ``stream_id`` is the DVD subtitle substream (0x20 + langidx). Pass ``None``
    to accept any subtitle substream in 0x20–0x3F — needed when the .idx
    langidx does not match the PES header.
    """
    spu = bytearray()
    spu_size: int | None = None
    pos = start
    hard_limit = len(data)

    while pos + 4 <= hard_limit:
        if data[pos : pos + 3] != b"\x00\x00\x01":
            break
        sid = data[pos + 3]

        if sid == 0xBA:  # pack header (MPEG-2: 14 bytes + stuffing)
            if pos + 14 > hard_limit:
                break
            stuffing = data[pos + 13] & 0x07
            pos += 14 + stuffing
            continue
        if sid in (0xBB, 0xBE, 0xBF):  # system header / padding
            if pos + 6 > hard_limit:
                break
            length = int.from_bytes(data[pos + 4 : pos + 6], "big")
            pos += 6 + length
            continue
        if sid != _PES_PRIVATE1:
            # Some other PES (e.g. video/audio) — skip by length.
            if pos + 6 > hard_limit:
                break
            length = int.from_bytes(data[pos + 4 : pos + 6], "big")
            pos += 6 + length
            continue

        # private_stream_1 PES
        pes_len = int.from_bytes(data[pos + 4 : pos + 6], "big")
        ext_start = pos + 6
        pes_end = ext_start + pes_len
        if pes_end > hard_limit or pes_len < 3:
            break
        hdr_len = data[ext_start + 2]
        data_off = ext_start + 3 + hdr_len
        if data_off >= pes_end:
            pos = pes_end
            continue
        substream = data[data_off]
        chunk = data[data_off + 1 : pes_end]
        if stream_id is None:
            accept = 0x20 <= substream <= 0x3F
        else:
            accept = substream == stream_id
        if accept:
            spu.extend(chunk)
            if spu_size is None and len(spu) >= 2:
                spu_size = int.from_bytes(spu[:2], "big")
            if spu_size is not None and len(spu) >= spu_size:
                break
        pos = pes_end

        # Stop once we've consumed up to the next event's offset and have data.
        if pos >= limit and spu:
            if spu_size is None or len(spu) >= spu_size:
                break

    if not spu:
        return None
    if spu_size is not None:
        return bytes(spu[:spu_size])
    return bytes(spu)


def _decode_spu(spu: bytes, info: _IdxInfo) -> tuple[Image.Image, float] | None:
    from PIL import Image  # noqa: PLC0415

    if len(spu) < 4:
        return None
    dcsq_offset = int.from_bytes(spu[2:4], "big")

    palette_idx = [0, 1, 2, 3]
    alpha = [0, 15, 15, 15]
    coords: tuple[int, int, int, int] | None = None
    rle_top = 0
    rle_bottom = 0
    duration_ms = 0.0
    start_delay = 0.0
    stop_delay = 0.0

    pos = dcsq_offset
    guard = 0
    while 0 <= pos < len(spu) - 3 and guard < 64:
        guard += 1
        delay = int.from_bytes(spu[pos : pos + 2], "big")
        next_dcsq = int.from_bytes(spu[pos + 2 : pos + 4], "big")
        delay_ms = delay * 1024.0 / 90.0  # (delay*1024)/90000 seconds → ms
        cmd_pos = pos + 4

        while cmd_pos < len(spu):
            cmd = spu[cmd_pos]
            cmd_pos += 1
            if cmd == 0xFF:
                break
            if cmd == 0x00 or cmd == 0x01:  # forced start display
                start_delay = delay_ms
            elif cmd == 0x02:  # stop display
                stop_delay = delay_ms
            elif cmd == 0x03:  # palette (4 x 4-bit indices)
                b0, b1 = spu[cmd_pos], spu[cmd_pos + 1]
                palette_idx = [b0 >> 4, b0 & 0x0F, b1 >> 4, b1 & 0x0F]
                cmd_pos += 2
            elif cmd == 0x04:  # alpha (4 x 4-bit)
                b0, b1 = spu[cmd_pos], spu[cmd_pos + 1]
                alpha = [b0 >> 4, b0 & 0x0F, b1 >> 4, b1 & 0x0F]
                cmd_pos += 2
            elif cmd == 0x05:  # display coordinates
                b = spu[cmd_pos : cmd_pos + 6]
                x1 = (b[0] << 4) | (b[1] >> 4)
                x2 = ((b[1] & 0x0F) << 8) | b[2]
                y1 = (b[3] << 4) | (b[4] >> 4)
                y2 = ((b[4] & 0x0F) << 8) | b[5]
                coords = (x1, y1, x2, y2)
                cmd_pos += 6
            elif cmd == 0x06:  # RLE data offsets (top, bottom fields)
                rle_top = int.from_bytes(spu[cmd_pos : cmd_pos + 2], "big")
                rle_bottom = int.from_bytes(spu[cmd_pos + 2 : cmd_pos + 4], "big")
                cmd_pos += 4
            else:
                break

        if stop_delay > start_delay:
            duration_ms = stop_delay - start_delay
        if next_dcsq == 0 or next_dcsq <= pos:
            break
        pos = next_dcsq

    if coords is None or rle_top == 0:
        return None
    x1, y1, x2, y2 = coords
    width = x2 - x1 + 1
    height = y2 - y1 + 1
    if width <= 0 or height <= 0 or width > 4096 or height > 4096:
        return None

    index_map = _decode_rle_image(spu, rle_top, rle_bottom, width, height)
    if index_map is None:
        return None

    image = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    px = image.load()
    palette = info.palette
    for y in range(height):
        row = index_map[y]
        for x in range(width):
            color_idx = row[x]
            a = alpha[color_idx]
            if a == 0:
                continue
            pal_entry = palette_idx[color_idx]
            r, g, b = palette[pal_entry] if pal_entry < len(palette) else (255, 255, 255)
            px[x, y] = (r, g, b, min(255, a * 17))

    return image, duration_ms


def _decode_rle_image(
    spu: bytes, top_off: int, bottom_off: int, width: int, height: int
) -> list[list[int]] | None:
    try:
        rows: list[list[int]] = [[0] * width for _ in range(height)]
        _decode_rle_field(spu, top_off, width, height, rows, start_row=0)
        _decode_rle_field(spu, bottom_off, width, height, rows, start_row=1)
    except (IndexError, ValueError):
        return None
    return rows


class _NibbleReader:
    __slots__ = ("data", "byte_pos", "high")

    def __init__(self, data: bytes, offset: int) -> None:
        self.data = data
        self.byte_pos = offset
        self.high = True

    def read(self) -> int:
        byte = self.data[self.byte_pos]
        if self.high:
            self.high = False
            return byte >> 4
        self.high = True
        self.byte_pos += 1
        return byte & 0x0F

    def align_byte(self) -> None:
        if not self.high:
            self.high = True
            self.byte_pos += 1


def _decode_rle_field(
    spu: bytes,
    offset: int,
    width: int,
    height: int,
    rows: list[list[int]],
    *,
    start_row: int,
) -> None:
    reader = _NibbleReader(spu, offset)
    y = start_row
    while y < height:
        x = 0
        while x < width:
            value = reader.read()
            if value < 0x4:
                value = (value << 4) | reader.read()
                if value < 0x10:
                    value = (value << 4) | reader.read()
                    if value < 0x40:
                        value = (value << 4) | reader.read()
            run = value >> 2
            color = value & 0x3
            if run == 0:
                run = width - x  # fill to end of line
            run = min(run, width - x)
            row = rows[y]
            for i in range(x, x + run):
                row[i] = color
            x += run
        reader.align_byte()
        y += 2
