import json
import struct
from pathlib import Path

from corrcut.engine.waveform import (
    PEAK_MAGIC,
    PeakIndex,
    PeakStore,
    preload_peak_memory,
    store_needs_preload,
)


def _make_store(tmp_path: Path, peak_count: int) -> PeakStore:
    data_path = tmp_path / "peaks.bin"
    payload = b"".join(struct.pack("<ff", -0.5, 0.5) for _ in range(peak_count))
    data_path.write_bytes(PEAK_MAGIC + payload)

    index_path = tmp_path / "index.json"
    index_path.write_text(
        json.dumps(
            {
                "levels": 1,
                "peaks_per_second_base": peak_count,
                "sample_rate": 48000,
                "duration_seconds": 1.0,
                "channel_key": "full",
            }
        ),
        encoding="utf-8",
    )
    meta = json.loads(index_path.read_text(encoding="utf-8"))
    index = PeakIndex(
        levels=1,
        peaks_per_second_base=int(meta["peaks_per_second_base"]),
        sample_rate=48000,
        duration_seconds=1.0,
        channel_key="full",
    )
    return PeakStore(index_path=index_path, data_path=data_path, index=index)


def test_store_needs_preload_until_memory_filled(tmp_path: Path) -> None:
    store = _make_store(tmp_path, 4)
    assert store_needs_preload(store)
    preload_peak_memory(store)
    assert not store_needs_preload(store)
    assert store.peaks_for_level_if_ready(0) is not None


def test_building_store_skips_preload(tmp_path: Path) -> None:
    from corrcut.domain.dialogue_target import DialogueTargetStrategy
    from corrcut.domain.project import TrackRecord

    track = TrackRecord(
        stream_index=1,
        codec="aac",
        channel_layout="stereo",
        channels=2,
        sample_rate=48000,
        language="eng",
        dialogue_target_strategy=DialogueTargetStrategy.FULL_MIX,
    )
    store = PeakStore.create_building(
        tmp_path,
        {"head_sha256": "z"},
        track,
        duration_seconds=0.5,
    )
    assert store.is_building
    assert not store_needs_preload(store)
