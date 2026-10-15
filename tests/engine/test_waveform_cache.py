import json
import struct
from pathlib import Path

from corrcut.engine.waveform import PEAK_HEADER_SIZE, PEAK_MAGIC, PeakIndex, PeakStore


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


def test_peaks_for_level_caches_in_memory(tmp_path: Path) -> None:
    store = _make_store(tmp_path, 4)
    a = store.peaks_for_level(0)
    b = store.peaks_for_level(0)
    assert a is b
    assert len(a) == 4
