"""Structured logging baseline."""

from __future__ import annotations

import logging
import sys
from pathlib import Path


def configure_logging(log_dir: Path | None = None, level: int = logging.INFO) -> None:
    """Configure root logger for console and optional file output."""
    handlers: list[logging.Handler] = [
        logging.StreamHandler(sys.stdout),
    ]
    if log_dir is not None:
        log_dir.mkdir(parents=True, exist_ok=True)
        file_handler = logging.FileHandler(log_dir / "corrcut.log", encoding="utf-8")
        file_handler.setFormatter(
            logging.Formatter("%(asctime)s %(levelname)s [%(name)s] %(message)s")
        )
        handlers.append(file_handler)

    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
        handlers=handlers,
        force=True,
    )
