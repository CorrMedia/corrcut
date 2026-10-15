"""Living-room speaker map for choosing which channels an edit applies to."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from PySide6.QtCore import QPoint, QRect, QSize, Qt, Signal
from PySide6.QtGui import QColor, QFont, QMouseEvent, QPainter, QPalette, QPen
from PySide6.QtWidgets import QSizePolicy, QWidget

from corrcut.ui.theme import contrast_text_on, muted_text_on

_FRONT = ("FLC", "FL", "FC", "FR", "FRC")
_SIDE = ("SL", "SR")
_REAR = ("BL", "BC", "BR")
_KNOWN = frozenset(_FRONT + _SIDE + _REAR)

_LABELS = {
    "FL": "Left",
    "FR": "Right",
    "FC": "Center",
    "BL": "Behind L",
    "BR": "Behind R",
    "SL": "Side L",
    "SR": "Side R",
    "BC": "Behind",
    "FLC": "Wide L",
    "FRC": "Wide R",
}

_PAD = 6
_TV_H = 16
_SPK_H = 36
_COUCH_H = 18
_GAP = 6
_LABEL_H = 12
_DISABLE_X = QColor("#e23d3d")


def speaker_label(name: str, present: frozenset[str] | set[str]) -> str:
    """Short living-room name; SL/SR read as behind when there is no BL/BR pair."""
    if name == "SL" and "BL" not in present:
        return "Behind L"
    if name == "SR" and "BR" not in present:
        return "Behind R"
    return _LABELS.get(name, name)


def _has_rear_bed(present: set[str]) -> bool:
    return any(name in present for name in _REAR)


def _has_sides(present: set[str]) -> bool:
    return any(name in present for name in _SIDE)


def room_height(names: Sequence[str]) -> int:
    present = set(names)
    extras = [name for name in names if name not in _KNOWN]
    height = _PAD + _TV_H + 2 + _SPK_H + _GAP + _COUCH_H
    if _has_rear_bed(present) or _has_sides(present):
        height += _GAP + _SPK_H
    if extras:
        height += _GAP + _SPK_H
    return height + _PAD


@dataclass(frozen=True)
class RoomGeometry:
    tv: QRect
    couch: QRect
    speakers: dict[str, QRect]


def room_geometry(names: Sequence[str], bounds: QRect) -> RoomGeometry:
    """Place TV, couch, and speakers in a compact living-room plan."""
    present = set(names)
    inner = bounds.adjusted(_PAD, _PAD, -_PAD, -_PAD)
    if inner.width() < 48 or inner.height() < 40:
        inner = QRect(bounds)

    wide = "FLC" in present or "FRC" in present
    cols = 5 if wide else 3
    col_w = inner.width() / cols
    center_col = cols // 2

    def col_rect(col: int, top: int, height: int) -> QRect:
        x = int(inner.x() + col * col_w)
        w = max(1, int(inner.x() + (col + 1) * col_w) - x)
        return QRect(x, top, w, height)

    def center_band(top: int, height: int, width_frac: float) -> QRect:
        band = col_rect(center_col, top, height)
        width = max(36, int(inner.width() * width_frac))
        width = min(width, inner.width() - 8)
        return QRect(
            inner.x() + (inner.width() - width) // 2,
            band.y(),
            width,
            height,
        )

    y = inner.y()
    tv = center_band(y, _TV_H, 0.38)
    front_top = y
    fc_top = y + _TV_H + 2
    front_bottom = fc_top + _SPK_H
    speakers: dict[str, QRect] = {}

    front_slots = {
        "FLC": 0,
        "FL": 1 if wide else 0,
        "FC": center_col,
        "FR": 3 if wide else 2,
        "FRC": 4,
    }
    for name, col in front_slots.items():
        if name not in present:
            continue
        if name == "FC":
            speakers[name] = col_rect(col, fc_top, _SPK_H)
        else:
            speakers[name] = col_rect(col, front_top, front_bottom - front_top)

    couch_top = front_bottom + _GAP
    couch = center_band(couch_top, _COUCH_H, 0.34)
    sides_own_row = _has_sides(present) and _has_rear_bed(present)
    if sides_own_row:
        if "SL" in present:
            speakers["SL"] = col_rect(0, couch_top - 8, _SPK_H)
        if "SR" in present:
            speakers["SR"] = col_rect(cols - 1, couch_top - 8, _SPK_H)

    next_top = couch_top + _COUCH_H + _GAP
    if _has_rear_bed(present):
        rear_slots = {"BL": 0, "BC": center_col, "BR": cols - 1}
        for name, col in rear_slots.items():
            if name in present:
                speakers[name] = col_rect(col, next_top, _SPK_H)
        next_top += _SPK_H + _GAP
    elif _has_sides(present):
        if "SL" in present:
            speakers["SL"] = col_rect(0, next_top, _SPK_H)
        if "SR" in present:
            speakers["SR"] = col_rect(cols - 1, next_top, _SPK_H)
        next_top += _SPK_H + _GAP

    extras = [name for name in names if name not in _KNOWN]
    if extras:
        extra_w = max(1, inner.width() // len(extras))
        for index, name in enumerate(extras):
            speakers[name] = QRect(
                inner.x() + index * extra_w,
                next_top,
                extra_w,
                _SPK_H,
            )

    return RoomGeometry(tv=tv, couch=couch, speakers=speakers)


class ChannelSpeakerHandle:
    """QCheckBox-compatible handle used by the operations editor."""

    def __init__(self, room: ChannelRoomWidget, name: str) -> None:
        self._room = room
        self._name = name

    def isChecked(self) -> bool:  # noqa: N802
        return self._room.is_checked(self._name)

    def setChecked(self, checked: bool) -> None:  # noqa: N802
        self._room.set_checked(self._name, checked)


class ChannelRoomWidget(QWidget):
    """Clickable TV / couch / speaker plan. Checked speakers are in the edit."""

    changed = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._names: list[str] = []
        self._checked: dict[str, bool] = {}
        self._handles: dict[str, ChannelSpeakerHandle] = {}
        self._hover: str | None = None
        self.setMouseTracking(True)
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Maximum)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setAccessibleName("Speaker layout")

    @property
    def checks(self) -> dict[str, ChannelSpeakerHandle]:
        return self._handles

    def set_channels(self, names: Sequence[str]) -> None:
        self._names = list(names)
        self._checked = {name: False for name in self._names}
        self._handles = {name: ChannelSpeakerHandle(self, name) for name in self._names}
        self._hover = None
        self.updateGeometry()
        self.update()

    def is_checked(self, name: str) -> bool:
        return bool(self._checked.get(name))

    def set_checked(self, name: str, checked: bool) -> None:
        if name not in self._checked or self._checked[name] is checked:
            return
        self._checked[name] = checked
        self.update()
        self.changed.emit()

    def all_checked(self) -> bool:
        return bool(self._names) and all(self._checked.get(name) for name in self._names)

    def toggle_all(self) -> None:
        """Check every speaker, or uncheck them all if every speaker is already on."""
        if not self._names:
            return
        target = not self.all_checked()
        changed = False
        for name in self._names:
            if self._checked[name] is not target:
                self._checked[name] = target
                changed = True
        if not changed:
            return
        self.update()
        self.changed.emit()

    def speaker_rect(self, name: str) -> QRect:
        return self._geometry().speakers.get(name, QRect())

    def tv_rect(self) -> QRect:
        return self._geometry().tv

    def sizeHint(self) -> QSize:  # noqa: N802
        return QSize(220, room_height(self._names) if self._names else 72)

    def minimumSizeHint(self) -> QSize:  # noqa: N802
        return self.sizeHint()

    def _geometry(self) -> RoomGeometry:
        return room_geometry(self._names, self.rect())

    def _hit(self, pos: QPoint) -> str | None:
        for name, rect in self._geometry().speakers.items():
            if rect.contains(pos):
                return name
        return None

    def mouseMoveEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        name = self._hit(event.position().toPoint())
        if name != self._hover:
            self._hover = name
            self.setCursor(
                Qt.CursorShape.PointingHandCursor if name else Qt.CursorShape.ArrowCursor
            )
            self.update()
        if name:
            present = frozenset(self._names)
            state = "included in this edit" if self.is_checked(name) else "not in this edit"
            self.setToolTip(f"{speaker_label(name, present)} ({name}) — {state}")
        else:
            self.setToolTip("")

    def leaveEvent(self, event) -> None:  # noqa: N802, ANN001
        self._hover = None
        self.setCursor(Qt.CursorShape.ArrowCursor)
        super().leaveEvent(event)
        self.update()

    def mousePressEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if event.button() != Qt.MouseButton.LeftButton:
            return
        name = self._hit(event.position().toPoint())
        if name is None:
            return
        self.set_checked(name, not self.is_checked(name))

    def paintEvent(self, event) -> None:  # noqa: N802, ANN001, ARG002
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
        palette = self.palette()
        base = palette.color(QPalette.ColorRole.Base)
        window = palette.color(QPalette.ColorRole.Window)
        highlight = palette.color(QPalette.ColorRole.Highlight)
        text = contrast_text_on(base, palette.color(QPalette.ColorRole.Text))
        muted = muted_text_on(base, text)
        geo = self._geometry()
        present = frozenset(self._names)

        room = QColor(base)
        room = room.darker(108) if room.lightness() > 128 else room.lighter(112)
        painter.setPen(QPen(QColor(muted), 1))
        painter.setBrush(room)
        painter.drawRoundedRect(self.rect().adjusted(0, 0, -1, -1), 6, 6)

        self._paint_tv(painter, geo.tv, window, muted)
        self._paint_couch(painter, geo.couch, highlight)
        for name, rect in geo.speakers.items():
            self._paint_speaker(
                painter,
                name,
                rect,
                present,
                checked=self.is_checked(name),
                hovered=name == self._hover,
                highlight=highlight,
                text=text,
                muted=muted,
                base=base,
            )

    def _paint_tv(
        self, painter: QPainter, rect: QRect, window: QColor, muted: QColor
    ) -> None:
        if rect.width() < 8 or rect.height() < 6:
            return
        bezel = QColor(muted)
        bezel.setAlpha(220)
        screen = QColor(window)
        screen = screen.darker(130) if screen.lightness() > 128 else screen.lighter(118)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(bezel)
        painter.drawRoundedRect(rect, 2, 2)
        glass = rect.adjusted(3, 2, -3, -3)
        painter.setBrush(screen)
        painter.drawRoundedRect(glass, 1, 1)

    def _paint_couch(self, painter: QPainter, rect: QRect, highlight: QColor) -> None:
        if rect.width() < 16 or rect.height() < 8:
            return
        arm_w = max(4, rect.width() // 12)
        body = rect.adjusted(arm_w, 4, -arm_w, 0)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(highlight.darker(145))
        painter.drawRoundedRect(
            QRect(body.x(), rect.y(), body.width(), max(5, rect.height() // 2)),
            4,
            4,
        )
        painter.setBrush(highlight)
        painter.drawRoundedRect(body, 3, 3)
        painter.setBrush(highlight.darker(120))
        painter.drawRoundedRect(QRect(rect.x(), rect.y() + 4, arm_w, rect.height() - 4), 3, 3)
        painter.drawRoundedRect(
            QRect(rect.right() - arm_w + 1, rect.y() + 4, arm_w, rect.height() - 4),
            3,
            3,
        )

    def _paint_speaker(
        self,
        painter: QPainter,
        name: str,
        rect: QRect,
        present: frozenset[str],
        *,
        checked: bool,
        hovered: bool,
        highlight: QColor,
        text: QColor,
        muted: QColor,
        base: QColor,
    ) -> None:
        label_rect = QRect(rect.x(), rect.bottom() - _LABEL_H + 1, rect.width(), _LABEL_H)
        cab_area = rect.adjusted(4, 2, -4, -(_LABEL_H + 1))
        cab = self._cabinet_rect(name, cab_area)
        cab_color = QColor(base)
        if checked:
            cab_color = cab_color.darker(130) if cab_color.lightness() > 128 else cab_color.lighter(130)
        elif hovered:
            cab_color = cab_color.lighter(118) if cab_color.lightness() < 128 else cab_color.darker(108)
        else:
            cab_color = cab_color.darker(112) if cab_color.lightness() > 128 else cab_color.lighter(124)
        ring = muted if checked else highlight
        painter.setPen(QPen(ring, 1))
        painter.setBrush(cab_color)
        painter.drawRoundedRect(cab, 2, 2)
        self._paint_cones(painter, name, cab, highlight if not checked else muted)
        if checked:
            self._paint_disable_x(painter, cab)
        font = QFont(self.font())
        font.setPixelSize(9)
        font.setWeight(QFont.Weight.DemiBold)
        painter.setFont(font)
        painter.setPen(muted if checked else text)
        painter.drawText(
            label_rect,
            int(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter),
            speaker_label(name, present),
        )

    def _paint_disable_x(self, painter: QPainter, rect: QRect) -> None:
        if rect.width() < 6 or rect.height() < 6:
            return
        painter.setPen(QPen(_DISABLE_X, 2))
        inset_x = max(2, rect.width() // 6)
        inset_y = max(2, rect.height() // 5)
        painter.drawLine(
            rect.left() + inset_x,
            rect.top() + inset_y,
            rect.right() - inset_x,
            rect.bottom() - inset_y,
        )
        painter.drawLine(
            rect.right() - inset_x,
            rect.top() + inset_y,
            rect.left() + inset_x,
            rect.bottom() - inset_y,
        )

    def _cabinet_rect(self, name: str, area: QRect) -> QRect:
        if area.width() < 4 or area.height() < 4:
            return area
        if name == "FC":
            w = min(area.width(), 56)
            h = min(area.height(), 16)
            return QRect(
                area.x() + (area.width() - w) // 2,
                area.bottom() - h,
                w,
                h,
            )
        if name in {"FL", "FR", "FLC", "FRC"}:
            w = min(area.width(), 14)
            h = min(area.height(), 26)
        else:
            w = min(area.width(), 16)
            h = min(area.height(), 16)
        return QRect(
            area.x() + (area.width() - w) // 2,
            area.y() + (area.height() - h) // 2,
            w,
            h,
        )

    def _paint_cones(
        self, painter: QPainter, name: str, cab: QRect, color: QColor
    ) -> None:
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(color)
        if name == "FC":
            count = 3
            size = max(3, min(cab.height() - 4, cab.width() // 6))
        elif name in {"FL", "FR", "FLC", "FRC"}:
            count = 2
            size = max(3, min(7, cab.width() - 6))
        else:
            count = 1
            size = max(4, min(cab.width(), cab.height()) - 6)
        gap = 2
        total = count * size + (count - 1) * gap
        if name in {"FL", "FR", "FLC", "FRC"}:
            y = cab.y() + (cab.height() - total) // 2
            x = cab.x() + (cab.width() - size) // 2
            for i in range(count):
                painter.drawEllipse(x, y + i * (size + gap), size, size)
        else:
            x = cab.x() + (cab.width() - total) // 2
            y = cab.y() + (cab.height() - size) // 2
            for i in range(count):
                painter.drawEllipse(x + i * (size + gap), y, size, size)
