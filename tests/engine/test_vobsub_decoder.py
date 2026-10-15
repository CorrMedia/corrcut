"""Tests for the VobSub (.idx/.sub) bitmap decoder."""

from __future__ import annotations

from pathlib import Path

import pytest

from corrcut.engine.subtitles.vobsub_decoder import decode_vobsub, parse_idx

_FIXTURE = Path(__file__).parent.parent / "fixtures" / "vobsub" / "img_sub.idx"


def test_parse_idx_reads_size_palette_and_entries() -> None:
    info = parse_idx(_FIXTURE)
    assert info.width == 1280
    assert info.height == 720
    assert len(info.entries) == 2
    assert info.stream_id == 0x20
    # Palette index 1 is white in the fixture.
    assert info.palette[1] == (255, 255, 255)


def test_decode_vobsub_produces_images_with_timing() -> None:
    events = decode_vobsub(_FIXTURE)
    assert len(events) == 2
    first, second = events
    assert first.start_ms == 0.0
    assert second.start_ms == 1000.0
    for event in events:
        assert event.image.mode == "RGBA"
        w, h = event.image.size
        assert 0 < w <= 1280
        assert 0 < h <= 720
        # Decoded bitmap has visible (non-transparent) pixels.
        assert event.image.getbbox() is not None


def test_decode_vobsub_missing_sub_raises(tmp_path: Path) -> None:
    idx = tmp_path / "orphan.idx"
    idx.write_text("size: 720x480\n", encoding="utf-8")
    with pytest.raises(FileNotFoundError):
        decode_vobsub(idx)


def test_read_spu_accepts_any_subtitle_substream_when_id_omitted() -> None:
    from corrcut.engine.subtitles import vobsub_decoder as decoder

    info = parse_idx(_FIXTURE)
    sub = _FIXTURE.with_suffix(".sub").read_bytes()
    start = info.entries[0][1]
    limit = info.entries[1][1]
    assert decoder._read_spu(sub, start, limit, info.stream_id) is not None
    assert decoder._read_spu(sub, start, limit, info.stream_id + 1) is None
    assert decoder._read_spu(sub, start, limit, None) is not None


def test_decode_vobsub_recovers_from_wrong_langidx(monkeypatch: pytest.MonkeyPatch) -> None:
    from corrcut.engine.subtitles import vobsub_decoder as decoder

    info = parse_idx(_FIXTURE)
    wrong = decoder._IdxInfo(
        width=info.width,
        height=info.height,
        palette=info.palette,
        entries=info.entries,
        stream_id=info.stream_id + 1,
    )
    monkeypatch.setattr(decoder, "parse_idx", lambda _path: wrong)
    events = decode_vobsub(_FIXTURE)
    assert len(events) == 2
    assert events[0].image.getbbox() is not None
