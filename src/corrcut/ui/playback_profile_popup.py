"""Popup for choosing which categories and actions Edited preview applies."""

from __future__ import annotations

from PySide6.QtCore import QPoint, Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (
    QCheckBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QStyle,
    QStyleOptionButton,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from corrcut.domain.corr_categories import category_branches
from corrcut.preview.playback_profile import (
    PLAYBACK_ACTIONS,
    PlaybackProfile,
)
from corrcut.ui.buttons import use_pill_button

_POPUP_WIDTH = 440
_POPUP_MAX_HEIGHT = 560

_STYLE = """
QFrame#playbackProfilePopup {
    background: #1b1b1b;
    border: 1px solid #3f3f3f;
    border-radius: 10px;
}
QScrollArea#playbackProfileScroll {
    background: transparent;
    border: none;
}
QWidget#playbackProfileBody {
    background: transparent;
}
QFrame#playbackProfileSection {
    background: #262626;
    border: 1px solid #3a3a3a;
    border-radius: 8px;
}
QCheckBox {
    color: #e8e8e8;
    spacing: 8px;
    padding: 3px 4px;
    border-radius: 4px;
}
QCheckBox:hover {
    background: rgba(255, 255, 255, 0.06);
}
QCheckBox::indicator {
    width: 15px;
    height: 15px;
    border-radius: 4px;
    border: 1px solid #6a6a6a;
    background: #1e1e1e;
}
QCheckBox::indicator:hover {
    border-color: #9a9a9a;
}
QCheckBox::indicator:checked {
    background: #d7dde6;
    border-color: #d7dde6;
}
QCheckBox::indicator:indeterminate {
    background: #e6c36a;
    border-color: #e6c36a;
}
QLabel#playbackProfileTitle {
    color: #f2f2f2;
    font-size: 14px;
    font-weight: 600;
}
QLabel#playbackProfileHint {
    color: #9a9a9a;
}
QLabel#playbackProfileGroup {
    color: #c8c8c8;
    font-size: 12px;
    font-weight: 600;
}
QWidget#playbackProfileWordsPanel {
    background: #202020;
    border-radius: 6px;
}
QToolButton#playbackProfileWords {
    color: #e4e4e4;
    background: #333333;
    border: 1px solid #5c5c5c;
    border-radius: 10px;
    padding: 2px 10px;
    font-size: 11px;
}
QToolButton#playbackProfileWords:hover {
    background: #3c3c3c;
    border-color: #9a9a9a;
    color: #ffffff;
}
QToolButton#playbackProfileWords:checked {
    background: #3a3428;
    border-color: #e6c36a;
    color: #f3e2b8;
}
"""


class _MarkCheckBox(QCheckBox):
    """Dark-panel checkbox. The stylesheet paints the box; this paints the mark."""

    def paintEvent(self, event) -> None:  # noqa: ANN001, N802
        super().paintEvent(event)
        state = self.checkState()
        if state == Qt.CheckState.Unchecked:
            return
        option = QStyleOptionButton()
        self.initStyleOption(option)
        rect = self.style().subElementRect(QStyle.SubElement.SE_CheckBoxIndicator, option, self)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        pen = QPen(QColor("#1a1a1a"))
        pen.setWidthF(1.8)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        painter.setPen(pen)
        if state == Qt.CheckState.PartiallyChecked:
            mid = rect.center().y()
            painter.drawLine(rect.left() + 4, mid, rect.right() - 4, mid)
        else:
            path = QPainterPath()
            path.moveTo(rect.left() + 3.5, rect.center().y())
            path.lineTo(rect.center().x() - 0.4, rect.bottom() - 4.0)
            path.lineTo(rect.right() - 3.2, rect.top() + 4.2)
            painter.drawPath(path)
        painter.end()


class _GroupCheckBox(_MarkCheckBox):
    """Parent checkbox. A click turns the whole group on or off, never the partial state."""

    def nextCheckState(self) -> None:  # noqa: N802
        if self.checkState() == Qt.CheckState.Checked:
            self.setCheckState(Qt.CheckState.Unchecked)
        else:
            self.setCheckState(Qt.CheckState.Checked)


class PlaybackProfilePopup(QFrame):
    """Category tree and action list. Emits a profile each time the selection changes."""

    profile_changed = Signal(object)

    def __init__(self, profile: PlaybackProfile, parent: QWidget | None = None) -> None:
        super().__init__(parent, Qt.WindowType.Popup | Qt.WindowType.FramelessWindowHint)
        self.setObjectName("playbackProfilePopup")
        self.setStyleSheet(_STYLE)
        self.setFixedWidth(_POPUP_WIDTH)
        self._enabled: set[str] = set(profile.enabled_categories())
        self._actions: set[str] = set(profile.enabled_actions())
        self._child_ids: dict[str, tuple[str, ...]] = {}
        self._checks: dict[str, list[QCheckBox]] = {}
        self._groups: dict[str, list[_GroupCheckBox]] = {}
        self._action_checks: dict[str, QCheckBox] = {}
        self._last = profile

        root = QVBoxLayout(self)
        root.setContentsMargins(14, 12, 14, 12)
        root.setSpacing(10)

        header = QHBoxLayout()
        header.setSpacing(8)
        title = QLabel("Playback profile")
        title.setObjectName("playbackProfileTitle")
        header.addWidget(title)
        header.addStretch(1)
        all_btn = QPushButton("All edits")
        all_btn.setObjectName("playbackProfileAll")
        use_pill_button(all_btn)
        all_btn.clicked.connect(self._select_all)
        header.addWidget(all_btn)
        root.addLayout(header)

        hint = QLabel("Same categories and actions CorrMedia applies during playback.")
        hint.setObjectName("playbackProfileHint")
        hint.setWordWrap(True)
        root.addWidget(hint)

        scroll = QScrollArea()
        scroll.setObjectName("playbackProfileScroll")
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        body = QWidget()
        body.setObjectName("playbackProfileBody")
        body_layout = QVBoxLayout(body)
        body_layout.setContentsMargins(0, 0, 6, 0)
        body_layout.setSpacing(8)
        self._build_actions(body_layout)
        self._build_categories(body_layout)
        body_layout.addStretch(1)
        scroll.setWidget(body)
        root.addWidget(scroll, 1)

        self._refresh_widgets()
        self._fit_height()

    def show_beside(self, anchor: QWidget) -> None:
        """Open under *anchor*, flipping above it when the screen is short."""
        self._fit_height()
        pos = anchor.mapToGlobal(QPoint(0, anchor.height() + 4))
        screen = anchor.screen()
        if screen is not None:
            avail = screen.availableGeometry()
            if pos.x() + self.width() > avail.right():
                pos.setX(max(avail.left(), avail.right() - self.width()))
            if pos.y() + self.height() > avail.bottom():
                above = anchor.mapToGlobal(QPoint(0, 0)).y() - self.height() - 4
                pos.setY(max(avail.top(), above))
        self.move(pos)
        self.show()

    def _fit_height(self) -> None:
        hint = self.sizeHint().height()
        self.setFixedHeight(max(280, min(_POPUP_MAX_HEIGHT, hint)))

    def _section(self, title: str, layout: QVBoxLayout) -> QVBoxLayout:
        card = QFrame()
        card.setObjectName("playbackProfileSection")
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(10, 8, 10, 8)
        card_layout.setSpacing(4)
        label = QLabel(title)
        label.setObjectName("playbackProfileGroup")
        card_layout.addWidget(label)
        layout.addWidget(card)
        return card_layout

    def _build_categories(self, layout: QVBoxLayout) -> None:
        for group, branches in category_branches():
            card = self._section(group, layout)
            grid = QGridLayout()
            grid.setContentsMargins(0, 0, 0, 0)
            grid.setHorizontalSpacing(8)
            grid.setVerticalSpacing(0)
            grid.setColumnStretch(0, 1)
            grid.setColumnStretch(1, 1)
            row = 0
            col = 0
            for branch in branches:
                parent = branch.option
                if branch.children:
                    if col:
                        row += 1
                        col = 0
                    host = QWidget()
                    host_layout = QVBoxLayout(host)
                    host_layout.setContentsMargins(0, 0, 0, 0)
                    host_layout.setSpacing(2)
                    self._add_group(host_layout, parent.id, parent.label, branch.children)
                    grid.addWidget(host, row, 0, 1, 2)
                    row += 1
                    continue
                grid.addWidget(self._make_check(parent.id, parent.label), row, col)
                col += 1
                if col == 2:
                    col = 0
                    row += 1
            card.addLayout(grid)

    def _build_actions(self, layout: QVBoxLayout) -> None:
        card = self._section("Actions", layout)
        grid = QGridLayout()
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(8)
        grid.setVerticalSpacing(0)
        grid.setColumnStretch(0, 1)
        grid.setColumnStretch(1, 1)
        for index, (action_id, action_label) in enumerate(PLAYBACK_ACTIONS):
            box = _MarkCheckBox(action_label)
            box.setObjectName(f"playback-action-{action_id}")
            box.setCursor(Qt.CursorShape.PointingHandCursor)
            box.clicked.connect(lambda _checked=False, aid=action_id: self._on_action_clicked(aid))
            self._action_checks[action_id] = box
            grid.addWidget(box, index // 2, index % 2)
        card.addLayout(grid)

    def _add_group(
        self,
        layout: QVBoxLayout,
        parent_id: str,
        label: str,
        children: tuple,
    ) -> None:
        child_ids = tuple(child.id for child in children)
        self._child_ids.setdefault(parent_id, child_ids)
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(4)
        box = _GroupCheckBox(label)
        box.setObjectName(f"playback-cat-{parent_id}")
        box.setTristate(True)
        box.setCursor(Qt.CursorShape.PointingHandCursor)
        box.clicked.connect(lambda _checked=False, pid=parent_id: self._on_parent_clicked(pid))
        self._groups.setdefault(parent_id, []).append(box)
        row.addWidget(box, 1)
        words = QToolButton()
        words.setObjectName("playbackProfileWords")
        words.setCheckable(True)
        words.setFixedHeight(22)
        words.setCursor(Qt.CursorShape.PointingHandCursor)
        words.setToolTip(f"Choose individual words in {label}")
        row.addWidget(words)
        layout.addLayout(row)

        child_host = QWidget()
        child_host.setObjectName("playbackProfileWordsPanel")
        child_grid = QGridLayout(child_host)
        child_grid.setContentsMargins(8, 6, 8, 6)
        child_grid.setHorizontalSpacing(8)
        child_grid.setVerticalSpacing(0)
        for index, child in enumerate(children):
            child_grid.addWidget(self._make_check(child.id, child.label), index // 2, index % 2)
        partial = any(child.id not in self._enabled for child in children) and any(
            child.id in self._enabled for child in children
        )
        child_host.setVisible(partial)
        words.setChecked(partial)
        words.setText("Words  ▴" if partial else "Words  ▾")
        words.toggled.connect(
            lambda checked, host=child_host, button=words: self._on_words_toggled(
                button, host, checked
            )
        )
        layout.addWidget(child_host)

    def _make_check(self, cat_id: str, label: str) -> QCheckBox:
        box = _MarkCheckBox(label)
        box.setObjectName(f"playback-cat-{cat_id}")
        box.setCursor(Qt.CursorShape.PointingHandCursor)
        box.clicked.connect(lambda _checked=False, cid=cat_id: self._on_category_clicked(cid))
        self._checks.setdefault(cat_id, []).append(box)
        return box

    def _on_words_toggled(self, button: QToolButton, host: QWidget, checked: bool) -> None:
        host.setVisible(checked)
        button.setText("Words  ▴" if checked else "Words  ▾")

    def _on_parent_clicked(self, parent_id: str) -> None:
        child_ids = self._child_ids.get(parent_id, ())
        all_on = parent_id in self._enabled and all(cid in self._enabled for cid in child_ids)
        self._set_ids((parent_id, *child_ids), enabled=not all_on)

    def _on_category_clicked(self, cat_id: str) -> None:
        self._set_ids((cat_id,), enabled=cat_id not in self._enabled)

    def _on_action_clicked(self, action_id: str) -> None:
        if action_id in self._actions:
            self._actions.discard(action_id)
        else:
            self._actions.add(action_id)
        self._refresh_widgets()
        self._emit()

    def _select_all(self) -> None:
        self._enabled = set(PlaybackProfile.full().enabled_categories())
        self._actions = set(PlaybackProfile.full().enabled_actions())
        self._refresh_widgets()
        self._emit()

    def _set_ids(self, cat_ids: tuple[str, ...], *, enabled: bool) -> None:
        for cat_id in cat_ids:
            if enabled:
                self._enabled.add(cat_id)
            else:
                self._enabled.discard(cat_id)
        self._reconcile_parents()
        self._refresh_widgets()
        self._emit()

    def _reconcile_parents(self) -> None:
        """A parent id stays on only while every one of its words is on."""
        for parent_id, child_ids in self._child_ids.items():
            if child_ids and all(cid in self._enabled for cid in child_ids):
                self._enabled.add(parent_id)
            else:
                self._enabled.discard(parent_id)

    def _refresh_widgets(self) -> None:
        for cat_id, boxes in self._checks.items():
            checked = cat_id in self._enabled
            for box in boxes:
                box.blockSignals(True)
                box.setChecked(checked)
                box.blockSignals(False)
        for parent_id, boxes in self._groups.items():
            state = self._group_state(parent_id)
            for box in boxes:
                box.blockSignals(True)
                box.setCheckState(state)
                box.blockSignals(False)
        for action_id, box in self._action_checks.items():
            box.blockSignals(True)
            box.setChecked(action_id in self._actions)
            box.blockSignals(False)

    def _group_state(self, parent_id: str) -> Qt.CheckState:
        child_ids = self._child_ids.get(parent_id, ())
        kids_on = [cid in self._enabled for cid in child_ids]
        if parent_id in self._enabled and all(kids_on):
            return Qt.CheckState.Checked
        if not any(kids_on):
            return Qt.CheckState.Unchecked
        return Qt.CheckState.PartiallyChecked

    def _emit(self) -> None:
        profile = PlaybackProfile.from_selection(categories=self._enabled, actions=self._actions)
        if profile == self._last:
            return
        self._last = profile
        self.profile_changed.emit(profile)
