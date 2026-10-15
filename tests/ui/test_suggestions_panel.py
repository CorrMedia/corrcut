"""Suggestions panel table behavior."""

from __future__ import annotations

from fractions import Fraction

from PySide6.QtWidgets import QApplication

from corrcut.domain.ai_suggestion import AiSuggestion, SuggestionStatus
from corrcut.domain.operation import TimeRange
from corrcut.domain.time_point import TimePoint
from corrcut.ui.suggestions_panel import SuggestionsPanel

_app = QApplication.instance() or QApplication([])


def _suggestion(start: float, end: float, *, word: str = "test") -> AiSuggestion:
    return AiSuggestion.create(
        stream_index=0,
        time_range=TimeRange(
            start=TimePoint(Fraction(start)),
            end=TimePoint(Fraction(end)),
        ),
        matched_word=word,
        matched_term=word,
        transcript_phrase=word,
        confidence=0.9,
        whisper_probability=0.9,
        alignment_quality="good",
    )


def test_suggestion_word_drops_sentence_punctuation() -> None:
    panel = SuggestionsPanel()
    panel.load_suggestions([_suggestion(0.0, 1.0, word="shit,")])
    word_item = panel._table.item(0, 2)
    assert word_item is not None
    assert word_item.text() == "shit"

    panel.load_suggestions([_suggestion(0.0, 1.0, word="fuckin'")])
    word_item = panel._table.item(0, 2)
    assert word_item is not None
    assert word_item.text() == "fuckin'"

    panel.load_suggestions([_suggestion(0.0, 1.0, word="well-known")])
    word_item = panel._table.item(0, 2)
    assert word_item is not None
    assert word_item.text() == "well-known"
    from PySide6.QtCore import QPoint, Qt
    from PySide6.QtTest import QTest

    from corrcut.ui.suggestions_panel import action_button_rects

    panel = SuggestionsPanel()
    panel.resize(480, 280)
    suggestions = [_suggestion(float(i), float(i) + 0.5, word=f"w{i}") for i in range(4)]
    panel.load_suggestions(suggestions)
    panel.show()
    QApplication.processEvents()

    accepted: list[str] = []
    rejected: list[str] = []
    panel.accept_requested.connect(accepted.append)
    panel.reject_requested.connect(rejected.append)
    table = panel._table
    assert table.cellWidget(0, 0) is None
    assert table.cellWidget(3, 0) is None

    def click_action(row: int, *, accept: bool) -> None:
        cell = table.visualRect(table.model().index(row, 0))
        accept_rect, reject_rect = action_button_rects(cell)
        target = accept_rect if accept else reject_rect
        QTest.mouseClick(
            table.viewport(),
            Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.NoModifier,
            QPoint(target.center()),
        )
        QApplication.processEvents()

    click_action(1, accept=True)
    assert accepted == [suggestions[1].id]
    assert rejected == []

    click_action(3, accept=False)
    assert rejected == [suggestions[3].id]
    assert accepted == [suggestions[1].id]

    row_item = table.item(1, 0)
    assert row_item is not None
    row_item.setData(panel._ROLE_STATUS, SuggestionStatus.ACCEPTED.value)
    table.viewport().repaint()
    QApplication.processEvents()
    image = table.viewport().grab().toImage()
    ratio = image.devicePixelRatio()

    def accept_fill(row: int) -> tuple[int, int, int]:
        cell = table.visualRect(table.model().index(row, 0))
        accept_rect, _reject_rect = action_button_rects(cell)
        point = QPoint(accept_rect.left() + 6, accept_rect.center().y())
        color = image.pixelColor(int(point.x() * ratio), int(point.y() * ratio))
        return color.red(), color.green(), color.blue()

    clicked_row = accept_fill(1)
    last_row = accept_fill(3)
    assert clicked_row[1] > clicked_row[0] + 40
    assert last_row[1] < last_row[0] + 20
    panel.close()


def test_reload_table_preserves_scroll_position() -> None:
    panel = SuggestionsPanel()
    panel.resize(400, 120)
    suggestions = [_suggestion(float(i), float(i) + 1, word=f"w{i}") for i in range(40)]
    panel.load_suggestions(suggestions)

    table = panel._table
    table.verticalHeader().setDefaultSectionSize(30)
    table.setFixedHeight(120)
    panel.show()
    QApplication.processEvents()

    scroll_bar = table.verticalScrollBar()
    scroll_bar.setValue(scroll_bar.maximum())
    scroll_pos = scroll_bar.value()
    assert scroll_pos > 0

    suggestions[10].status = SuggestionStatus.ACCEPTED
    panel.load_suggestions(suggestions)

    assert scroll_bar.value() == scroll_pos


def test_follow_playhead_selects_matching_suggestion_without_seek() -> None:
    panel = SuggestionsPanel()
    panel.resize(400, 120)
    suggestions = [
        _suggestion(0.0, 1.0, word="a"),
        _suggestion(2.0, 3.0, word="b"),
        _suggestion(4.0, 5.0, word="c"),
    ]
    panel.load_suggestions(suggestions)

    table = panel._table
    table.verticalHeader().setDefaultSectionSize(30)
    table.setFixedHeight(90)
    panel.show()
    QApplication.processEvents()

    seek_emitted: list[float] = []
    panel.seek_requested.connect(seek_emitted.append)

    panel.follow_playhead(2.5)
    QApplication.processEvents()

    assert table.currentRow() == 1
    assert seek_emitted == []
    assert table.verticalScrollBar().value() > 0


def test_follow_playhead_keeps_lingered_suggestion_selected() -> None:
    panel = SuggestionsPanel()
    suggestions = [_suggestion(1.0, 2.0, word="a")]
    panel.load_suggestions(suggestions)

    panel.follow_playhead(2.3)
    QApplication.processEvents()

    assert panel._table.currentRow() == 0


def test_busy_keeps_review_controls_and_shows_banner() -> None:
    panel = SuggestionsPanel()
    panel.load_suggestions([_suggestion(0.0, 1.0)])
    panel.set_busy(True)

    assert not panel._generate_btn.isEnabled()
    assert panel._filter_btn.isEnabled()
    assert panel._settings_btn.isEnabled()
    assert panel._table.isEnabled()

    panel.show_job_banner("Analyzing stream #1 (aac)")
    panel.set_job_progress(18, "Transcribing…")
    assert not panel._job_banner.isHidden()
    assert "Transcribing" in panel._job_banner.phase_text()

    cancelled: list[bool] = []
    panel.job_cancel_requested.connect(lambda: cancelled.append(True))
    panel._job_banner._cancel_btn.click()
    assert cancelled == [True]
    assert panel._job_banner.phase_text() == "Cancelling…"

    panel.hide_job_banner()
    assert panel._job_banner.isHidden()
