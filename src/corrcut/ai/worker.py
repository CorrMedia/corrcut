"""Subprocess entry point for optional separate AI venv (CORRCUT_AI_PYTHON)."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

from corrcut.engine.hidden_process import install_hidden_subprocess

install_hidden_subprocess()

from corrcut.ai.detection_result import DetectionResult  # noqa: E402
from corrcut.ai.run_detection_core import detect_from_media  # noqa: E402
from corrcut.domain.ai_suggestion import DetectionSettings  # noqa: E402
from corrcut.domain.dialogue_target import DialogueTargetStrategy  # noqa: E402


def _progress(pct: int, msg: str) -> None:
    print(f"PROGRESS {pct} {msg}", flush=True)


def _emit_result(result: DetectionResult) -> None:
    payload = {
        "suggestions": [s.to_json() for s in result.suggestions],
        "whisper_words": result.whisper_words_to_json(),
    }
    print("RESULT " + json.dumps(payload), flush=True)


def run_from_payload(data: dict[str, Any]) -> DetectionResult:
    settings = DetectionSettings.from_json(dict(data.get("settings", {})))
    track = dict(data.get("track", {}))
    source = Path(str(data["source_path"]))
    stream_index = int(track.get("stream_index", data.get("stream_index", 0)))
    strategy = DialogueTargetStrategy(str(track.get("dialogue_target_strategy", "full_mix")))
    layout = track.get("channel_layout")
    sample_rate = int(track["sample_rate"]) if track.get("sample_rate") else 16000
    language_raw = str(track["language"]) if track.get("language") else None
    duration = data.get("duration_seconds")
    duration_f = float(duration) if duration is not None else None
    subtitle_streams = list(data.get("subtitle_streams") or [])
    project_cues = data.get("project_subtitle_cues")
    project_subtitle_cues = (
        list(project_cues) if isinstance(project_cues, list) and project_cues else None
    )

    return detect_from_media(
        source,
        stream_index=stream_index,
        dialogue_target_strategy=strategy,
        channel_layout=str(layout) if layout else None,
        sample_rate=sample_rate,
        language_raw=language_raw,
        subtitle_streams=subtitle_streams,
        duration_seconds=duration_f,
        settings=settings,
        progress=_progress,
        log=lambda line: print(line, flush=True),
        project_subtitle_cues=project_subtitle_cues,
    )


def main() -> int:
    raw = sys.stdin.read()
    if not raw.strip():
        print("RESULT " + json.dumps({"suggestions": []}), flush=True)
        return 0
    data = json.loads(raw)
    suggestions = run_from_payload(data)
    _emit_result(suggestions)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
