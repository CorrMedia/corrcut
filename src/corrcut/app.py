"""Application bootstrap."""

from __future__ import annotations

import importlib.util
import logging
import sys

from corrcut.config import APP_NAME, AppPaths
from corrcut.engine.hidden_process import install_hidden_subprocess
from corrcut.logging_setup import configure_logging
from corrcut.ui.launcher import run_application

logger = logging.getLogger(__name__)


def run_app() -> int:
    install_hidden_subprocess()
    paths = AppPaths.default()
    paths.ensure_dirs()
    configure_logging(paths.log_dir)
    logger.info("Starting %s", APP_NAME)
    from corrcut.io.filmstrip_cache import schedule_filmstrip_cache_prune

    schedule_filmstrip_cache_prune(paths.cache_dir)
    if sys.version_info[:2] >= (3, 14):
        from corrcut.engine.subtitles.paddle_ocr_engine import suggested_ocr_python

        logger.warning(
            "Python %s does not support PaddleOCR. Launch with: %s -m corrcut",
            f"{sys.version_info.major}.{sys.version_info.minor}",
            suggested_ocr_python(),
        )
    if importlib.util.find_spec("mpv") is not None:
        from corrcut.preview.libmpv_loader import prepare_libmpv

        if prepare_libmpv(paths):
            logger.info("libmpv ready for preview")
        else:
            logger.warning("libmpv not available; video preview disabled")
    return run_application(paths)
