"""Background peak preload job result (UI thread applies to timeline)."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from corrcut.engine.waveform import PeakStore


@dataclass(frozen=True)
class PeakPreloadDone:
    token: int
    store: PeakStore


@dataclass(frozen=True)
class WaveformBuildDone:
    """Result from a background waveform cache build; generation invalidates stale results."""

    generation: int
    store: PeakStore


@dataclass(frozen=True)
class WaveformPeaksChunk:
    """Progressive base-level peaks from a streaming waveform build."""

    generation: int
    start_peak: int
    peaks: np.ndarray
