"""Off-thread media open: probe and sidecar. Subtitles load after the editor is up."""

from __future__ import annotations

import json
import threading
from pathlib import Path

import pytest

from corrcut.domain.dialogue_target import DialogueTargetStrategy
from corrcut.domain.operation import OperationType
from corrcut.engine.jobs.cancel import JobCancelled
from corrcut.engine.probe import AudioStreamInfo, MediaProbeResult
from corrcut.io.media_open import prepare_media_open


def _probe(path: Path) -> MediaProbeResult:
    return MediaProbeResult(
        path=path,
        format_name="matroska",
        duration_seconds=10.0,
        video_streams=(),
        audio_streams=(
            AudioStreamInfo(
                index=1,
                codec="aac",
                channel_layout="stereo",
                channels=2,
                sample_rate=48000,
                language="eng",
                title=None,
                is_default=True,
                dialogue_target_strategy=DialogueTargetStrategy.FULL_MIX,
            ),
        ),
    )


def test_prepare_loads_sibling_sidecar(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    media = tmp_path / "Title.mkv"
    media.write_bytes(b"x")
    sidecar = tmp_path / "Title.corr.json"
    sidecar.write_text(
        json.dumps(
            {
                "schema_version": "1.0",
                "edits": [{"id": "e1", "start": 1.0, "end": 2.0, "action": "mute"}],
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "corrcut.io.media_open.probe_media_cached",
        lambda path, cache_root, **_kwargs: (
            _probe(path),
            {"size": 1, "head_sha256": "abc", "tail_sha256": ""},
            True,
        ),
    )

    prepared = prepare_media_open(media, tmp_path / "cache")

    assert prepared.probe_cache_hit is True
    assert prepared.sidecar_loaded is True
    assert prepared.corr_path == sidecar
    assert prepared.sidecar_error is None
    assert len(prepared.project.operations) == 1
    assert prepared.project.operations[0].type == OperationType.MUTE
    assert prepared.project.subtitle_cues == []


def test_prepare_keeps_media_when_sidecar_is_invalid(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    media = tmp_path / "Title.mkv"
    media.write_bytes(b"x")
    (tmp_path / "Title.corr.json").write_text("{", encoding="utf-8")
    monkeypatch.setattr(
        "corrcut.io.media_open.probe_media_cached",
        lambda path, cache_root, **_kwargs: (
            _probe(path),
            {"size": 1, "head_sha256": "abc", "tail_sha256": ""},
            False,
        ),
    )

    prepared = prepare_media_open(media, tmp_path / "cache")

    assert prepared.sidecar_loaded is False
    assert prepared.sidecar_error
    assert prepared.project.operations == []
    assert prepared.project.source is not None
    assert prepared.project.source.path == media.resolve()


def test_prepare_stops_when_cancelled(tmp_path: Path) -> None:
    cancel = threading.Event()
    cancel.set()
    with pytest.raises(JobCancelled):
        prepare_media_open(tmp_path / "Title.mkv", tmp_path / "cache", cancel_event=cancel)
