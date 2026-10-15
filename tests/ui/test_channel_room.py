from PySide6.QtCore import QEvent, QPointF, QRect, Qt
from PySide6.QtGui import QMouseEvent

from corrcut.ui.channel_room import (
    ChannelRoomWidget,
    _DISABLE_X,
    room_geometry,
    speaker_label,
)


def test_speaker_labels_match_living_room() -> None:
    five_one = {"FC", "FL", "FR", "BL", "BR"}
    assert speaker_label("FC", five_one) == "Center"
    assert speaker_label("FL", five_one) == "Left"
    assert speaker_label("FR", five_one) == "Right"
    assert speaker_label("BL", five_one) == "Behind L"
    assert speaker_label("BR", five_one) == "Behind R"
    side = {"FC", "FL", "FR", "SL", "SR"}
    assert speaker_label("SL", side) == "Behind L"
    assert speaker_label("SR", side) == "Behind R"
    seven = {"FC", "FL", "FR", "BL", "BR", "SL", "SR"}
    assert speaker_label("SL", seven) == "Side L"
    assert speaker_label("SR", seven) == "Side R"


def test_51_places_speakers_around_tv_and_couch() -> None:
    geo = room_geometry(["FC", "FL", "FR", "BL", "BR"], QRect(0, 0, 220, 140))
    assert geo.speakers["FL"].center().x() < geo.tv.center().x()
    assert geo.speakers["FR"].center().x() > geo.tv.center().x()
    assert abs(geo.speakers["FC"].center().x() - geo.tv.center().x()) <= 8
    assert geo.speakers["FC"].top() >= geo.tv.bottom()
    assert not geo.speakers["FC"].intersects(geo.tv)
    assert geo.couch.center().y() > geo.speakers["FC"].bottom()
    assert geo.speakers["BL"].center().y() > geo.couch.center().y()
    assert geo.speakers["BL"].center().x() < geo.speakers["BR"].center().x()


def test_stereo_keeps_left_and_right_beside_tv() -> None:
    geo = room_geometry(["FL", "FR"], QRect(0, 0, 220, 100))
    assert "FC" not in geo.speakers
    assert geo.speakers["FL"].center().x() < geo.tv.center().x()
    assert geo.speakers["FR"].center().x() > geo.tv.center().x()


def test_disable_x_is_red() -> None:
    assert _DISABLE_X.red() > _DISABLE_X.green()
    assert _DISABLE_X.red() > _DISABLE_X.blue()


def test_hovering_center_does_not_raise(qtbot) -> None:
    room = ChannelRoomWidget()
    qtbot.addWidget(room)
    room.set_channels(["FC", "FL", "FR", "BL", "BR"])
    room.resize(220, 140)
    room.show()
    qtbot.waitExposed(room)
    event = QMouseEvent(
        QEvent.Type.MouseMove,
        QPointF(room.speaker_rect("FC").center()),
        Qt.MouseButton.NoButton,
        Qt.MouseButton.NoButton,
        Qt.KeyboardModifier.NoModifier,
    )
    room.mouseMoveEvent(event)
    assert room.cursor().shape() == Qt.CursorShape.PointingHandCursor


def test_tv_is_not_a_speaker_control(qtbot) -> None:
    room = ChannelRoomWidget()
    qtbot.addWidget(room)
    room.set_channels(["FC", "FL", "FR", "BL", "BR"])
    room.resize(220, 140)
    room.show()
    qtbot.waitExposed(room)
    room.set_checked("FC", True)
    with qtbot.assertNotEmitted(room.changed):
        qtbot.mouseClick(
            room,
            Qt.MouseButton.LeftButton,
            pos=room.tv_rect().center(),
        )
    assert room.checks["FC"].isChecked()


def test_clicking_center_speaker_toggles(qtbot) -> None:
    room = ChannelRoomWidget()
    qtbot.addWidget(room)
    room.set_channels(["FC", "FL", "FR", "BL", "BR"])
    room.resize(220, 140)
    room.show()
    qtbot.waitExposed(room)
    room.set_checked("FC", True)
    with qtbot.waitSignal(room.changed, timeout=1000):
        qtbot.mouseClick(
            room,
            Qt.MouseButton.LeftButton,
            pos=room.speaker_rect("FC").center(),
        )
    assert not room.checks["FC"].isChecked()


def test_clicking_speaker_toggles_and_emits(qtbot) -> None:
    room = ChannelRoomWidget()
    qtbot.addWidget(room)
    room.set_channels(["FC", "FL", "FR", "BL", "BR"])
    room.resize(220, 140)
    room.show()
    qtbot.waitExposed(room)
    room.set_checked("FC", True)
    assert room.checks["FC"].isChecked()
    with qtbot.waitSignal(room.changed, timeout=1000):
        qtbot.mouseClick(
            room,
            Qt.MouseButton.LeftButton,
            pos=room.speaker_rect("BL").center(),
        )
    assert room.checks["BL"].isChecked()
    assert room.checks["FC"].isChecked()


def test_toggle_all_selects_then_clears(qtbot) -> None:
    room = ChannelRoomWidget()
    qtbot.addWidget(room)
    room.set_channels(["FC", "FL", "FR", "BL", "BR"])
    room.set_checked("FC", True)
    assert not room.all_checked()
    with qtbot.waitSignal(room.changed, timeout=1000):
        room.toggle_all()
    assert room.all_checked()
    with qtbot.waitSignal(room.changed, timeout=1000):
        room.toggle_all()
    assert not any(room.is_checked(name) for name in room.checks)
