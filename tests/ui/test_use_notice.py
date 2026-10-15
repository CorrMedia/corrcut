"""The published use notice is on the welcome screen and in About."""

from __future__ import annotations

from PySide6.QtWidgets import QApplication, QLabel

from corrcut.notices import about_text, known_limits, use_notice
from corrcut.ui.welcome_window import WelcomeWindow

_app = QApplication.instance() or QApplication([])


def test_use_notice_states_playback_sidecar_terms() -> None:
    text = use_notice()
    assert "private home viewing" in text
    assert "corr.json" in text
    assert "does not create a new movie file" in text


def test_about_text_points_at_third_party_notices() -> None:
    text = about_text()
    assert use_notice() in text
    assert "LIMITS.txt" in text
    assert "THIRD_PARTY_NOTICES.txt" in text
    assert "licenses" in text
    assert "MIT" in text


def test_known_limits_state_first_release_bounds() -> None:
    text = known_limits()
    assert "64-bit Windows" in text
    assert ".corrcut" in text
    assert "corr.json" in text
    assert "does not contain the movie's picture or sound" in text
    assert "CPU build of PaddlePaddle" in text
    assert "does not queue several movies" in text
    assert "does not update itself" in text
    assert "corrcut-codesign.cer" in text
    assert "Trusted Root" in text


def test_welcome_window_shows_use_notice() -> None:
    window = WelcomeWindow()
    labels = [label.text() for label in window.findChildren(QLabel)]
    assert use_notice() in labels
    window.close()
