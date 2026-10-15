"""Application configuration and pinned external tool expectations."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

APP_NAME = "CorrCUT"
PROJECT_FILE_VERSION = 2

# Minimum tool versions (major.minor); patch ignored in comparison.
MIN_FFMPEG_VERSION = (6, 0)
MIN_FFPROBE_VERSION = (6, 0)
MIN_MKVMERGE_VERSION = (82, 0)


def env_value(suffix: str) -> str | None:
    """Read ``CORRCUT_<suffix>`` from the environment."""
    return os.environ.get(f"CORRCUT_{suffix}") or None


def app_settings():
    """QSettings store for CorrCUT window state, flags, and recent projects."""
    from PySide6.QtCore import QSettings

    return QSettings("corrcut", "CorrCUT")


@dataclass(frozen=True)
class AppPaths:
    """Runtime paths for cache, logs, and temp artifacts."""

    app_data: Path
    cache_dir: Path
    log_dir: Path
    temp_dir: Path

    @classmethod
    def default(cls) -> AppPaths:
        base = Path.home() / ".corrcut"
        return cls(
            app_data=base,
            cache_dir=base / "cache",
            log_dir=base / "logs",
            temp_dir=base / "temp",
        )

    def ensure_dirs(self) -> None:
        for path in (self.cache_dir, self.log_dir, self.temp_dir):
            path.mkdir(parents=True, exist_ok=True)
