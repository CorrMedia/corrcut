import json
import shutil
import struct
import subprocess
from pathlib import Path

import numpy as np
import pytest

from corrcut.domain.dialogue_target import DialogueTargetStrategy
from corrcut.domain.project import TrackRecord
from corrcut.engine.waveform import (
    PEAK_HEADER_SIZE,
    PEAK_MAGIC,
    PeakIndex,
    PeakStore,
    _PcmPeakReducer,
    _audio_filter_for_track,
    _channel_key_for_track,
    _downsample_peaks,
    _peak_window_time,
    _reduce_pcm_chunks,
    _samples_per_peak,
    _split_peak_halves,
    _split_peak_segments,
    _stream_ffmpeg_peaks,
)


def test_level_byte_offset_includes_header(tmp_path: Path) -> None:
    data_path = tmp_path / "peaks.bin"
    data_path.write_bytes(b"MSPK1" + b"\x00" * 80)

    index_path = tmp_path / "index.json"
    index_path.write_text(
        json.dumps(
            {
                "levels": 2,
                "peaks_per_second_base": 10,
                "sample_rate": 48000,
                "duration_seconds": 2.0,
                "channel_key": "full",
            }
        ),
        encoding="utf-8",
    )
    meta = json.loads(index_path.read_text(encoding="utf-8"))
    index = PeakIndex(
        levels=int(meta["levels"]),
        peaks_per_second_base=int(meta["peaks_per_second_base"]),
        sample_rate=int(meta["sample_rate"]),
        duration_seconds=float(meta["duration_seconds"]),
        channel_key=str(meta["channel_key"]),
    )
    store = PeakStore(index_path=index_path, data_path=data_path, index=index)

    assert store._level_byte_offset(0) == PEAK_HEADER_SIZE
    level0_count = store.total_peaks_at_level(0)
    assert store._level_byte_offset(1) == PEAK_HEADER_SIZE + level0_count * 8


def _track(
    layout: str | None,
    channels: int,
    strategy: DialogueTargetStrategy,
) -> TrackRecord:
    return TrackRecord(
        stream_index=1,
        codec="aac",
        channel_layout=layout,
        channels=channels,
        sample_rate=48000,
        language="eng",
        dialogue_target_strategy=strategy,
    )


def test_waveform_extracts_fc_only_when_layout_has_center() -> None:
    surround = _track("5.1(side)", 6, DialogueTargetStrategy.CENTER_CHANNEL)
    assert _channel_key_for_track(surround) == "FC"
    assert _audio_filter_for_track(surround, "FC") == "pan=mono|c0=FC"

    seven = _track("7.1(wide-side)", 8, DialogueTargetStrategy.CENTER_CHANNEL)
    assert _channel_key_for_track(seven) == "FC"

    stereo = _track("stereo", 2, DialogueTargetStrategy.FULL_MIX)
    assert _channel_key_for_track(stereo) == "stereo"
    assert _audio_filter_for_track(stereo, "stereo") == "pan=mono|c0=0.5*FL+0.5*FR"


def test_waveform_does_not_pan_missing_center_channel() -> None:
    # Stale 5.1 strategy on a stereo replacement must not extract silent FC.
    stale = _track("stereo", 2, DialogueTargetStrategy.CENTER_CHANNEL)
    assert _channel_key_for_track(stale) == "stereo"
    assert _audio_filter_for_track(stale, "stereo") == "pan=mono|c0=0.5*FL+0.5*FR"

    mismatched = _track("5.1", 2, DialogueTargetStrategy.CENTER_CHANNEL)
    assert _channel_key_for_track(mismatched) == "stereo"


def test_unlabeled_two_channel_track_uses_stereo_mix() -> None:
    unlabeled = _track(None, 2, DialogueTargetStrategy.FULL_MIX)
    assert _channel_key_for_track(unlabeled) == "stereo"
    assert _audio_filter_for_track(unlabeled, "stereo") == "pan=mono|c0=0.5*FL+0.5*FR"


def test_samples_per_peak_uses_planned_duration() -> None:
    assert _samples_per_peak(2.0, 48000, 400) == 240
    assert _samples_per_peak(10.0, 44100, 2000) == 220
    assert _samples_per_peak(0.001, 48000, 1) == 48


def _reference_peaks(samples: np.ndarray, target: int, spp: int) -> list[tuple[float, float]]:
    out: list[tuple[float, float]] = []
    total = int(samples.shape[0])
    for index in range(target):
        start = index * spp
        if start >= total:
            out.append((0.0, 0.0))
            continue
        window = samples[start : min(total, start + spp)]
        out.append((float(window.min()), float(window.max())))
    return out


def _awkward_chunks(raw: bytes) -> list[bytes]:
    chunks: list[bytes] = []
    offset = 0
    step = 1
    while offset < len(raw):
        chunks.append(raw[offset : offset + step])
        offset += step
        step = step % 7 + 1
    return chunks


def test_reduce_pcm_chunks_matches_window_minmax_across_splits() -> None:
    rng = np.random.default_rng(0)
    samples = rng.uniform(-1.5, 1.5, size=10_003).astype("<f4")
    raw = samples.tobytes()
    target = 30
    spp = 400
    expected = _reference_peaks(samples, target, spp)
    whole = _reduce_pcm_chunks([raw], target_peaks=target, samples_per_peak=spp)
    split = _reduce_pcm_chunks(_awkward_chunks(raw), target_peaks=target, samples_per_peak=spp)
    assert whole.shape == (target, 2)
    np.testing.assert_array_equal(split, whole)
    for index, (mn, mx) in enumerate(expected):
        assert split[index, 0] == pytest.approx(mn)
        assert split[index, 1] == pytest.approx(mx)
    # Three samples past the last full window stay out of the 25 complete peaks.
    assert int(samples.shape[0]) > 25 * spp
    assert split[25, 0] == pytest.approx(float(samples[25 * spp :].min()))
    assert split[26, 0] == pytest.approx(0.0)
    assert split[26, 1] == pytest.approx(0.0)


def test_reduce_pcm_chunks_pads_when_pcm_is_empty() -> None:
    peaks = _reduce_pcm_chunks([b"", b"\x00\x01"], target_peaks=4, samples_per_peak=8)
    assert peaks.shape == (4, 2)
    assert np.all(peaks == 0.0)


def test_downsample_peaks_pairs_minmax_and_keeps_odd_tail() -> None:
    peaks = np.array(
        [[-1.0, 0.2], [0.5, 0.4], [-0.25, 1.5]],
        dtype=np.float32,
    )
    down = _downsample_peaks(peaks)
    assert down.shape == (2, 2)
    assert down[0, 0] == pytest.approx(-1.0)
    assert down[0, 1] == pytest.approx(0.4)
    assert down[1, 0] == pytest.approx(-0.25)
    assert down[1, 1] == pytest.approx(1.5)


def test_stream_ffmpeg_peaks_matches_captured_pcm() -> None:
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg is None:
        pytest.skip("ffmpeg not on PATH")
    cmd = [
        ffmpeg,
        "-hide_banner",
        "-nostats",
        "-loglevel",
        "error",
        "-f",
        "lavfi",
        "-i",
        "sine=frequency=440:sample_rate=8000:duration=0.05",
        "-ac",
        "1",
        "-f",
        "f32le",
        "-",
    ]
    captured = subprocess.run(cmd, capture_output=True, check=False, timeout=30)
    if captured.returncode != 0 or not captured.stdout:
        pytest.skip("ffmpeg sine source failed")
    streamed = _stream_ffmpeg_peaks(cmd, target_peaks=4, samples_per_peak=100, timeout_s=30)
    reduced = _reduce_pcm_chunks([captured.stdout], target_peaks=4, samples_per_peak=100)
    np.testing.assert_array_equal(streamed, reduced)
    assert float(streamed[:, 1].max()) > 0.05
    assert float(streamed[:, 0].min()) < -0.05


def test_split_peak_halves_covers_full_range() -> None:
    assert _split_peak_halves(1) == [(0, 1)]
    assert _split_peak_halves(2) == [(0, 1), (1, 1)]
    assert _split_peak_halves(5) == [(0, 2), (2, 3)]
    covered = 0
    for start, count in _split_peak_halves(401):
        assert start == covered
        covered += count
    assert covered == 401


def test_split_peak_segments_covers_full_range() -> None:
    parts = _split_peak_segments(10, 4)
    assert len(parts) == 4
    covered = 0
    for start, count in parts:
        assert start == covered
        covered += count
    assert covered == 10
    assert _split_peak_segments(3, 8) == [(0, 1), (1, 1), (2, 1)]



def test_peak_window_time_maps_indices() -> None:
    start_s, dur_s = _peak_window_time(0, 100, duration_seconds=10.0, total_peaks=200)
    assert start_s == pytest.approx(0.0)
    assert dur_s == pytest.approx(5.0)
    start_s, dur_s = _peak_window_time(100, 100, duration_seconds=10.0, total_peaks=200)
    assert start_s == pytest.approx(5.0)
    assert dur_s == pytest.approx(5.0)


def test_reducer_emits_progress_chunks() -> None:
    rng = np.random.default_rng(1)
    samples = rng.uniform(-1.0, 1.0, size=1_000).astype("<f4")
    seen: list[tuple[int, int]] = []

    def _on_progress(start: int, chunk: np.ndarray) -> None:
        seen.append((start, int(chunk.shape[0])))

    reducer = _PcmPeakReducer(
        target_peaks=10,
        samples_per_peak=100,
        on_progress=_on_progress,
        progress_every=3,
        progress_offset=50,
    )
    reducer.feed(samples.tobytes())
    peaks = reducer.finish()
    assert peaks.shape == (10, 2)
    assert seen
    assert seen[0][0] == 50
    covered = sum(count for _start, count in seen)
    assert covered == 10


def test_building_store_applies_slices_and_finalizes(tmp_path: Path) -> None:
    track = _track("stereo", 2, DialogueTargetStrategy.FULL_MIX)
    store = PeakStore.create_building(
        tmp_path,
        {"head_sha256": "abc"},
        track,
        duration_seconds=1.0,
        sample_rate=48000,
    )
    assert store.is_building
    assert not store.has_displayable_peaks()
    total = store.total_peaks_at_level(0)
    assert total == 200
    first = np.array([[-0.5, 0.5], [-0.25, 0.75]], dtype=np.float32)
    store.apply_base_peak_slice(0, first)
    assert store.has_displayable_peaks()
    assert store.ready_count == 2
    assert store.peak_ready(0, 0)
    assert not store.peak_ready(0, 5)
    arrays = store.base_peak_arrays()
    assert arrays is not None
    peaks, ready = arrays
    assert peaks[0, 0] == pytest.approx(-0.5)
    assert bool(ready[0])
    base = np.zeros((total, 2), dtype=np.float32)
    base[0:2] = first
    store.finalize_from_base(base)
    assert not store.is_building
    assert store.index_path.is_file()
    assert store.data_path.is_file()
    assert len(store.peaks_for_level_if_ready(0) or []) == total
    assert store.peaks_for_level_if_ready(1) is not None


def test_dual_half_build_emits_chunks(tmp_path: Path) -> None:
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg is None:
        pytest.skip("ffmpeg not on PATH")
    media = tmp_path / "tone.wav"
    make = subprocess.run(
        [
            ffmpeg,
            "-hide_banner",
            "-nostats",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:sample_rate=8000:duration=0.4",
            "-y",
            str(media),
        ],
        capture_output=True,
        check=False,
        timeout=30,
    )
    if make.returncode != 0 or not media.is_file():
        pytest.skip("ffmpeg could not write tone fixture")

    track = TrackRecord(
        stream_index=0,
        codec="pcm_s16le",
        channel_layout="mono",
        channels=1,
        sample_rate=8000,
        language=None,
        dialogue_target_strategy=DialogueTargetStrategy.FULL_MIX,
    )
    chunks: list[int] = []

    def _on_chunk(start: int, peaks: np.ndarray) -> None:
        chunks.append(int(peaks.shape[0]))
        del start

    store = PeakStore.build(
        media,
        tmp_path / "cache",
        {"head_sha256": "tone"},
        track,
        ffmpeg=ffmpeg,
        on_chunk=_on_chunk,
    )
    assert not store.is_building
    assert store.has_displayable_peaks()
    assert chunks  # at least one progressive UI notify
    assert store.data_path.is_file()
    assert len(store.peaks_for_level_if_ready(0) or []) == store.total_peaks_at_level(0)


def test_numpy_peak_bytes_match_on_disk_mip_records(tmp_path: Path) -> None:
    peaks = np.array([[-0.5, 0.25], [0.0, 1.5]], dtype="<f4")
    data_path = tmp_path / "peaks.bin"
    payload = np.ascontiguousarray(peaks, dtype="<f4").tobytes()
    data_path.write_bytes(PEAK_MAGIC + payload)
    assert struct.unpack_from("<ff", payload, 0) == pytest.approx((-0.5, 0.25))

    index = PeakIndex(
        levels=1,
        peaks_per_second_base=2,
        sample_rate=48000,
        duration_seconds=1.0,
        channel_key="full",
    )
    store = PeakStore(index_path=tmp_path / "index.json", data_path=data_path, index=index)
    got = store.read_level_slice(0, 0, 2)
    assert got[0] == pytest.approx((-0.5, 0.25))
    assert got[1] == pytest.approx((0.0, 1.5))
