from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

from corrcut.ui.preview_panel import PreviewPanel

_app = QApplication.instance() or QApplication([])


class _FakeVolumeBackend:
    def __init__(self, *, volume: int = 80) -> None:
        self._volume = int(volume)
        self._muted = False

    def volume(self) -> int:
        return self._volume

    def set_volume(self, linear_percent: int) -> bool:
        self._volume = max(0, min(100, int(linear_percent)))
        return True

    def is_muted(self) -> bool:
        return self._muted

    def set_muted(self, muted: bool) -> bool:
        self._muted = bool(muted)
        return True


def test_clicking_volume_speaker_toggles_mute(qtbot) -> None:  # type: ignore[no-untyped-def]
    panel = PreviewPanel()
    qtbot.addWidget(panel)
    backend = _FakeVolumeBackend(volume=80)
    panel._backend = backend  # type: ignore[attr-defined]
    panel._sync_volume()  # type: ignore[attr-defined]

    assert backend.is_muted() is False
    assert panel._volume_slider.value() == 80  # type: ignore[attr-defined]
    assert "Mute preview" in panel._volume_btn.toolTip()  # type: ignore[attr-defined]

    qtbot.mouseClick(panel._volume_btn, Qt.MouseButton.LeftButton)  # type: ignore[attr-defined]
    assert backend.is_muted() is True
    assert panel._volume_slider.value() == 80  # type: ignore[attr-defined]
    assert "Unmute preview" in panel._volume_btn.toolTip()  # type: ignore[attr-defined]

    qtbot.mouseClick(panel._volume_btn, Qt.MouseButton.LeftButton)  # type: ignore[attr-defined]
    assert backend.is_muted() is False
    assert panel._volume_slider.value() == 80  # type: ignore[attr-defined]
    assert "Mute preview" in panel._volume_btn.toolTip()  # type: ignore[attr-defined]


def test_dragging_slider_unmutes_preview(qtbot) -> None:  # type: ignore[no-untyped-def]
    panel = PreviewPanel()
    qtbot.addWidget(panel)
    backend = _FakeVolumeBackend(volume=60)
    panel._backend = backend  # type: ignore[attr-defined]
    panel._sync_volume()  # type: ignore[attr-defined]
    panel._set_muted(True)  # type: ignore[attr-defined]
    assert backend.is_muted() is True

    panel._volume_slider.setValue(40)  # type: ignore[attr-defined]
    assert backend.is_muted() is False
    assert backend.volume() == 40


def test_clicking_speaker_at_zero_restores_previous_volume(qtbot) -> None:  # type: ignore[no-untyped-def]
    panel = PreviewPanel()
    qtbot.addWidget(panel)
    backend = _FakeVolumeBackend(volume=70)
    panel._backend = backend  # type: ignore[attr-defined]
    panel._sync_volume()  # type: ignore[attr-defined]
    panel._volume_slider.setValue(0)  # type: ignore[attr-defined]
    assert backend.is_muted() is True
    assert backend.volume() == 0

    qtbot.mouseClick(panel._volume_btn, Qt.MouseButton.LeftButton)  # type: ignore[attr-defined]
    assert backend.is_muted() is False
    assert backend.volume() == 70
    assert panel._volume_slider.value() == 70  # type: ignore[attr-defined]
