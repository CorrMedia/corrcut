"""Floating keyframed box editor for spatial video edits."""

from __future__ import annotations

from PySide6.QtCore import QPoint, QPointF, QRect, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPen, QRegion
from PySide6.QtWidgets import QHBoxLayout, QPushButton, QWidget

from corrcut.domain.spatial_keyframes import keep_box_visible, lock_box_to_source_aspect

_HANDLE = 14.0
_CHROME_H = 36
_CHROME_W = 252
_STRIP_W = 168


def _aspect_content_rect(host_w: float, host_h: float, aspect: float) -> QRectF:
    if host_w <= 1 or host_h <= 1:
        return QRectF(0, 0, max(1.0, host_w), max(1.0, host_h))
    widget_aspect = host_w / host_h
    if widget_aspect > aspect:
        content_w = host_h * aspect
        content_h = host_h
        x = (host_w - content_w) / 2.0
        y = 0.0
    else:
        content_w = host_w
        content_h = host_w / aspect
        x = 0.0
        y = (host_h - content_h) / 2.0
    return QRectF(x, y, content_w, content_h)


def _tool_flags() -> Qt.WindowType:
    return (
        Qt.WindowType.Tool
        | Qt.WindowType.FramelessWindowHint
        | Qt.WindowType.WindowDoesNotAcceptFocus
    )


def _norm_to_rect(content: QRectF, box: dict[str, float]) -> QRectF:
    return QRectF(
        content.left() + float(box["x"]) * content.width(),
        content.top() + float(box["y"]) * content.height(),
        float(box["width"]) * content.width(),
        float(box["height"]) * content.height(),
    )


def _paint_normalized_box(
    painter: QPainter,
    content: QRectF,
    box: dict[str, float],
    *,
    fill: QColor,
    edge: QColor,
    overflow_edge: QColor | None = None,
) -> QRectF:
    full = _norm_to_rect(content, box)
    visible = full.intersected(content)
    painter.setBrush(Qt.BrushStyle.NoBrush)
    if visible.isValid() and not visible.isEmpty():
        painter.fillRect(visible, fill)
        painter.setPen(QPen(edge, 2))
        painter.drawRect(visible)
    overflowing = overflow_edge is not None and (
        full.left() < content.left() - 0.5
        or full.top() < content.top() - 0.5
        or full.right() > content.right() + 0.5
        or full.bottom() > content.bottom() + 0.5
    )
    if overflowing:
        painter.setPen(QPen(overflow_edge, 2, Qt.PenStyle.DashLine))
        painter.drawRect(full)
    return full


def _snap_normalized_box(box: dict[str, float], *, overflow: bool = False) -> dict[str, float]:
    """Keep flush-to-edge drags at exact 0/1 so crop can cover the last pixels."""
    snapped = {key: float(box[key]) for key in ("x", "y", "width", "height")}
    if overflow:
        if -0.001 <= snapped["x"] <= 0.001:
            snapped["x"] = 0.0
        if -0.001 <= snapped["y"] <= 0.001:
            snapped["y"] = 0.0
        if 0.999 <= snapped["x"] + snapped["width"] <= 1.001:
            snapped["width"] = max(0.02, 1.0 - snapped["x"])
        if 0.999 <= snapped["y"] + snapped["height"] <= 1.001:
            snapped["height"] = max(0.02, 1.0 - snapped["y"])
        return snapped
    if snapped["x"] <= 0.001:
        snapped["x"] = 0.0
    if snapped["y"] <= 0.001:
        snapped["y"] = 0.0
    if snapped["x"] + snapped["width"] >= 0.999:
        snapped["width"] = max(0.02, 1.0 - snapped["x"])
    if snapped["y"] + snapped["height"] >= 0.999:
        snapped["height"] = max(0.02, 1.0 - snapped["y"])
    return snapped


def apply_overlay_drag(
    press: dict[str, float],
    mode: str,
    dx: float,
    dy: float,
    *,
    lock_source_aspect: bool = False,
) -> dict[str, float]:
    """Apply a move/resize delta in normalized picture space."""
    box = dict(press)
    overflow = not lock_source_aspect
    if mode == "move":
        box["x"] = float(box["x"]) + dx
        box["y"] = float(box["y"]) + dy
        if overflow:
            box = keep_box_visible(box)
        else:
            box["x"] = max(0.0, min(1.0 - box["width"], box["x"]))
            box["y"] = max(0.0, min(1.0 - box["height"], box["y"]))
    elif lock_source_aspect and mode.startswith("resize_"):
        box = locked_source_aspect_resize(press, mode, dx, dy)
    elif mode == "resize_br":
        box["width"] = max(0.02, float(box["width"]) + dx)
        box["height"] = max(0.02, float(box["height"]) + dy)
        if not overflow:
            box["width"] = min(1.0 - box["x"], box["width"])
            box["height"] = min(1.0 - box["y"], box["height"])
    elif mode == "resize_tl":
        new_x = min(box["x"] + box["width"] - 0.02, box["x"] + dx)
        new_y = min(box["y"] + box["height"] - 0.02, box["y"] + dy)
        if not overflow:
            new_x = max(0.0, new_x)
            new_y = max(0.0, new_y)
        box["width"] = box["x"] + box["width"] - new_x
        box["height"] = box["y"] + box["height"] - new_y
        box["x"] = new_x
        box["y"] = new_y
    elif mode == "resize_tr":
        new_y = min(box["y"] + box["height"] - 0.02, box["y"] + dy)
        if not overflow:
            new_y = max(0.0, new_y)
        box["width"] = max(0.02, float(box["width"]) + dx)
        if not overflow:
            box["width"] = min(1.0 - box["x"], box["width"])
        box["height"] = box["y"] + box["height"] - new_y
        box["y"] = new_y
    elif mode == "resize_bl":
        new_x = min(box["x"] + box["width"] - 0.02, box["x"] + dx)
        if not overflow:
            new_x = max(0.0, new_x)
        box["width"] = box["x"] + box["width"] - new_x
        box["height"] = max(0.02, float(box["height"]) + dy)
        if not overflow:
            box["height"] = min(1.0 - box["y"], box["height"])
        box["x"] = new_x
    if overflow and mode != "move":
        box = keep_box_visible(box)
    box = _snap_normalized_box(box, overflow=overflow)
    if lock_source_aspect:
        box = _keep_source_aspect_after_snap(box, mode)
    return box


def locked_source_aspect_resize(
    press: dict[str, float],
    mode: str,
    dx: float,
    dy: float,
) -> dict[str, float]:
    """Resize a zoom box from a corner while keeping normalized width == height."""
    press_w = max(0.02, float(press["width"]))
    press_h = max(0.02, float(press["height"]))

    def _size(proposed_w: float, proposed_h: float, max_w: float, max_h: float) -> float:
        size = proposed_w if abs(proposed_w - press_w) >= abs(proposed_h - press_h) else proposed_h
        return max(0.02, min(max_w, max_h, size))

    if mode == "resize_br":
        x0 = float(press["x"])
        y0 = float(press["y"])
        size = _size(press_w + dx, press_h + dy, 1.0 - x0, 1.0 - y0)
        return {"x": x0, "y": y0, "width": size, "height": size}
    if mode == "resize_tl":
        right = float(press["x"]) + press_w
        bottom = float(press["y"]) + press_h
        size = _size(press_w - dx, press_h - dy, right, bottom)
        return {"x": right - size, "y": bottom - size, "width": size, "height": size}
    if mode == "resize_tr":
        x0 = float(press["x"])
        bottom = float(press["y"]) + press_h
        size = _size(press_w + dx, press_h - dy, 1.0 - x0, bottom)
        return {"x": x0, "y": bottom - size, "width": size, "height": size}
    if mode == "resize_bl":
        right = float(press["x"]) + press_w
        y0 = float(press["y"])
        size = _size(press_w - dx, press_h + dy, right, 1.0 - y0)
        return {"x": right - size, "y": y0, "width": size, "height": size}
    return dict(press)


def _keep_source_aspect_after_snap(box: dict[str, float], mode: str) -> dict[str, float]:
    size = min(float(box["width"]), float(box["height"]))
    if mode == "resize_tl":
        right = float(box["x"]) + float(box["width"])
        bottom = float(box["y"]) + float(box["height"])
        return {"x": right - size, "y": bottom - size, "width": size, "height": size}
    if mode == "resize_tr":
        bottom = float(box["y"]) + float(box["height"])
        return {"x": float(box["x"]), "y": bottom - size, "width": size, "height": size}
    if mode == "resize_bl":
        right = float(box["x"]) + float(box["width"])
        return {"x": right - size, "y": float(box["y"]), "width": size, "height": size}
    return {"x": float(box["x"]), "y": float(box["y"]), "width": size, "height": size}


class _GhostLayer(QWidget):
    """Full-host paint-only layer (click-through) for inactive keyframe poses."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        # Top-level tool windows ignore WA_TransparentForMouseEvents on Windows.
        # WindowTransparentForInput lets clicks fall through to the caption.
        self.setWindowFlags(_tool_flags() | Qt.WindowType.WindowTransparentForInput)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent, False)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setStyleSheet("background: transparent;")
        self._ghosts: list[dict[str, float]] = []
        self._path: list[dict[str, float]] = []
        self._active: dict[str, float] | None = None
        self._content = QRectF(0, 0, 1, 1)
        self.hide()

    def set_content_rect(self, rect: QRectF) -> None:
        self._content = QRectF(rect)

    def set_boxes(
        self,
        ghost: dict[str, float] | list[dict[str, float]] | None,
        active: dict[str, float] | None,
        path: list[dict[str, float]] | None = None,
    ) -> None:
        if isinstance(ghost, dict):
            ghosts = [ghost]
        elif isinstance(ghost, list):
            ghosts = ghost
        else:
            ghosts = []
        self._ghosts = [dict(item) for item in ghosts if isinstance(item, dict)]
        self._path = [dict(item) for item in path if isinstance(item, dict)] if path else []
        self._active = dict(active) if isinstance(active, dict) else None
        if not self._ghosts and len(self._path) < 2:
            self.hide()
            return
        self.update()

    def layout_on_host(self, host: QWidget) -> None:
        if not self._ghosts and len(self._path) < 2:
            self.hide()
            return
        top_left = host.mapToGlobal(QPoint(0, 0))
        self.setGeometry(QRect(top_left, host.size()))
        self.show()
        self.raise_()
        self.update()

    def clear(self) -> None:
        self._ghosts = []
        self._path = []
        self._active = None
        self.hide()
        self.resize(0, 0)

    def paintEvent(self, event) -> None:  # noqa: N802
        if not self._ghosts and len(self._path) < 2:
            return
        painter = QPainter(self)
        painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceOver)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(QPen(QColor(255, 255, 255, 110), 2, Qt.PenStyle.DashLine))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        for ghost in self._ghosts:
            full = _norm_to_rect(self._content, ghost)
            visible = full.intersected(self._content)
            if visible.isValid() and not visible.isEmpty():
                painter.fillRect(visible, QColor(255, 255, 255, 28))
            painter.drawRect(full)
        path = self._path if len(self._path) >= 2 else []
        if not path and self._active is not None and self._ghosts:
            path = [*self._ghosts, self._active]
        if len(path) >= 2:
            painter.setPen(QPen(QColor(180, 220, 255, 170), 1.25, Qt.PenStyle.SolidLine))
            centers = [_norm_to_rect(self._content, pose).center() for pose in path]
            for left, right in zip(centers, centers[1:], strict=False):
                painter.drawLine(left, right)


class _PoseStrip(QWidget):
    """Clickable time strip showing authored box positions."""

    index_clicked = Signal(int)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFixedSize(_STRIP_W, 26)
        self.setToolTip("Click a position to edit it")
        self._offsets: list[float] = [0.0]
        self._active = 0

    def set_poses(self, offsets: list[float], active: int) -> None:
        self._offsets = [max(0.0, min(1.0, float(value))) for value in offsets] or [0.0]
        self._active = max(0, min(int(active), len(self._offsets) - 1))
        self.update()

    def _dot_center(self, offset: float) -> QPointF:
        pad = 9.0
        y = self.height() / 2.0
        x = pad + offset * max(1.0, self.width() - pad * 2.0)
        return QPointF(x, y)

    def _hit_index(self, pos: QPointF) -> int | None:
        best: int | None = None
        best_dist = 11.0
        for index, offset in enumerate(self._offsets):
            center = self._dot_center(offset)
            dist = (center - pos).manhattanLength()
            if dist < best_dist:
                best_dist = dist
                best = index
        return best

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        mid = self.height() / 2.0
        pad = 9.0
        painter.setPen(QPen(QColor(255, 255, 255, 50), 2))
        painter.drawLine(QPointF(pad, mid), QPointF(self.width() - pad, mid))
        for index, offset in enumerate(self._offsets):
            center = self._dot_center(offset)
            active = index == self._active
            radius = 6.0 if active else 4.5
            if active:
                painter.setBrush(QColor(0, 220, 255, 255))
                painter.setPen(QPen(QColor(180, 245, 255, 255), 1.5))
            else:
                painter.setBrush(QColor(20, 24, 28, 230))
                painter.setPen(QPen(QColor(220, 220, 220, 200), 1.5))
            painter.drawEllipse(center, radius, radius)

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() != Qt.MouseButton.LeftButton:
            event.ignore()
            return
        pos = event.position() if hasattr(event, "position") else QPointF(event.pos())
        index = self._hit_index(pos)
        if index is None:
            event.ignore()
            return
        self.index_clicked.emit(index)
        event.accept()


class VideoRegionOverlay(QWidget):
    """Full-host Tool overlay: paints the active box in video picture coords.

    Sized to the embed widget (not the box) so DPI/letterbox cannot shear a
    separately-scaled window. Mouse hits only chrome + box via a window mask.
    """

    box_changed = Signal(dict)
    drag_active_changed = Signal(bool)
    endpoint_changed = Signal(str)
    keyframe_index_changed = Signal(int)
    add_keyframe_requested = Signal()
    remove_keyframe_requested = Signal()
    double_clicked = Signal(QPoint)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowFlags(_tool_flags())
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent, False)
        self.setMouseTracking(True)
        self.setStyleSheet("background: transparent;")
        self.setToolTip("Drag inside to move · drag a corner handle to resize")

        self._chrome = QWidget(self)
        self._chrome.setFixedSize(_CHROME_W, _CHROME_H)
        self._chrome.setStyleSheet(
            "QWidget#regionChrome { background: rgba(20, 24, 28, 220); border-radius: 6px; }"
            "QPushButton { color: #ddd; background: transparent; border: 1px solid #555; "
            "border-radius: 4px; padding: 2px 8px; }"
            "QPushButton:checked { background: #1a6a8a; border-color: #3ec8ff; color: #fff; }"
            "QPushButton:disabled { color: #666; border-color: #444; }"
        )
        self._chrome.setObjectName("regionChrome")
        row = QHBoxLayout(self._chrome)
        row.setContentsMargins(8, 4, 8, 4)
        row.setSpacing(6)
        self._pose_strip = _PoseStrip(self._chrome)
        self._add_btn = QPushButton("+")
        self._add_btn.setFixedWidth(28)
        self._add_btn.setToolTip("Add a position at the playhead")
        self._del_btn = QPushButton("−")
        self._del_btn.setFixedWidth(28)
        self._del_btn.setToolTip("Remove this position")
        row.addWidget(self._pose_strip, stretch=1)
        row.addWidget(self._add_btn)
        row.addWidget(self._del_btn)
        self._pose_strip.index_clicked.connect(
            lambda index: self._set_keyframe_index(index, emit=True)
        )
        self._add_btn.clicked.connect(self.add_keyframe_requested.emit)
        self._del_btn.clicked.connect(self.remove_keyframe_requested.emit)
        self._chrome.hide()

        self._ghost_layer = _GhostLayer(parent)

        self._active = False
        self._interactive = True
        self._keyframe_index = 0
        self._keyframe_count = 1
        self._keyframe_offsets: list[float] = [0.0]
        self._box = {"x": 0.35, "y": 0.35, "width": 0.3, "height": 0.3}
        self._ghosts: list[dict[str, float]] = []
        self._path: list[dict[str, float]] = []
        self._content_aspect = 16 / 9
        self._lock_source_aspect = False
        self._content = QRectF(0, 0, 1, 1)
        self._drag_mode: str | None = None
        self._press_global: QPointF | None = None
        self._press_box: dict[str, float] | None = None
        self._syncing = False
        self._preview_playing = False
        self._caption_hit = QRect()
        self.hide()

    @property
    def is_active(self) -> bool:
        return self._active

    @property
    def is_dragging(self) -> bool:
        return self._drag_mode is not None

    @property
    def endpoint(self) -> str:
        if self._keyframe_count <= 1 or self._keyframe_index <= 0:
            return "start"
        if self._keyframe_index >= self._keyframe_count - 1:
            return "end"
        return "keyframe"

    @property
    def keyframe_index(self) -> int:
        return self._keyframe_index

    @property
    def keyframe_count(self) -> int:
        return self._keyframe_count

    def set_keyframe_index(self, index: int) -> None:
        index = max(0, int(index))
        if index >= self._keyframe_count:
            self._keyframe_count = index + 1
        self._set_keyframe_index(index, emit=False)

    def set_sampled_box(
        self,
        box: dict[str, float],
        ghosts: list[dict[str, float]] | None = None,
    ) -> None:
        """Move the overlay to a playhead-sampled pose without remounting."""
        if not self._active or self._drag_mode is not None:
            return
        self._box = {
            "x": float(box["x"]),
            "y": float(box["y"]),
            "width": float(box["width"]),
            "height": float(box["height"]),
        }
        if ghosts is not None:
            self._ghosts = [dict(item) for item in ghosts if isinstance(item, dict)]
        if self._lock_source_aspect:
            self._box = lock_box_to_source_aspect(self._box)
            if ghosts is not None:
                self._ghosts = [lock_box_to_source_aspect(item) for item in self._ghosts]
        self._ghost_layer.set_boxes(self._ghosts, self._box, path=self._path)
        self._refresh_mask()
        self.update()

    def set_caption_hit_rect(self, rect: QRect | None) -> None:
        """Exclude on-screen captions from mouse hits so they stay editable."""
        self._caption_hit = QRect(rect) if rect is not None and rect.isValid() else QRect()
        if self._active:
            self._refresh_mask()

    def set_content_aspect(self, width: int | None, height: int | None) -> None:
        if width and height and width > 0 and height > 0:
            self._content_aspect = width / height

    @property
    def content_aspect(self) -> float:
        return self._content_aspect

    def set_preview_playing(self, playing: bool) -> None:
        """Hide zoom-box chrome during play; punch-in is in the picture instead."""
        self._preview_playing = bool(playing)
        if self._hides_during_playback():
            self.hide()
            self._ghost_layer.hide()
            return
        if self._active:
            self.show()
            self.raise_()

    def _hides_during_playback(self) -> bool:
        return self._preview_playing and self._lock_source_aspect

    def clear(self) -> None:
        self._release_drag()
        self._active = False
        self._interactive = True
        self._ghosts = []
        self._path = []
        self._keyframe_index = 0
        self._keyframe_count = 1
        self._keyframe_offsets = [0.0]
        self._ghost_layer.clear()
        # clearMask() makes the whole host hittable. A dismissed tool window
        # then sits on the caption and swallows double-clicks.
        self._set_input_transparent(True)
        self.setMask(QRegion())
        self.hide()
        self.resize(0, 0)

    def _set_input_transparent(self, enabled: bool) -> None:
        flag = Qt.WindowType.WindowTransparentForInput
        if bool(self.windowFlags() & flag) == enabled:
            return
        self.setWindowFlag(flag, enabled)

    def set_region(
        self,
        box: dict[str, float] | None,
        *,
        full_frame: bool = False,
        interactive: bool = True,
        ghost: dict[str, float] | list[dict[str, float]] | None = None,
        ghosts: list[dict[str, float]] | None = None,
        path: list[dict[str, float]] | None = None,
        endpoint: str = "start",
        keyframe_index: int | None = None,
        keyframe_count: int | None = None,
        keyframe_offsets: list[float] | None = None,
        lock_source_aspect: bool = False,
    ) -> None:
        if full_frame or box is None:
            self.clear()
            return
        self._lock_source_aspect = bool(lock_source_aspect)
        self._box = {
            "x": float(box["x"]),
            "y": float(box["y"]),
            "width": float(box["width"]),
            "height": float(box["height"]),
        }
        if self._lock_source_aspect:
            self._box = lock_box_to_source_aspect(self._box)
        if ghosts is not None:
            ghost_list = ghosts
        elif isinstance(ghost, list):
            ghost_list = ghost
        elif isinstance(ghost, dict):
            ghost_list = [ghost]
        else:
            ghost_list = []
        self._ghosts = [dict(item) for item in ghost_list if isinstance(item, dict)]
        self._path = [dict(item) for item in path if isinstance(item, dict)] if path else []
        if self._lock_source_aspect:
            self._ghosts = [lock_box_to_source_aspect(item) for item in self._ghosts]
            self._path = [lock_box_to_source_aspect(item) for item in self._path]
        count = keyframe_count if keyframe_count is not None else max(1, len(self._ghosts) + 1)
        self._keyframe_count = max(1, int(count))
        if keyframe_offsets:
            self._keyframe_offsets = [float(value) for value in keyframe_offsets]
        else:
            if self._keyframe_count <= 1:
                self._keyframe_offsets = [0.0]
            else:
                last = max(1, self._keyframe_count - 1)
                self._keyframe_offsets = [index / last for index in range(self._keyframe_count)]
        if len(self._keyframe_offsets) != self._keyframe_count:
            self._keyframe_count = max(1, len(self._keyframe_offsets))
        if keyframe_index is not None:
            index = int(keyframe_index)
        elif endpoint == "end":
            index = self._keyframe_count - 1
        else:
            index = 0
        self._active = True
        self._interactive = interactive
        self._syncing = True
        try:
            self._set_keyframe_index(index, emit=False)
        finally:
            self._syncing = False
        self._ghost_layer.set_boxes(self._ghosts, self._box, path=self._path)

    def layout_on_host(
        self,
        host: QWidget,
        *,
        content_rect: QRectF | None = None,
    ) -> None:
        if not self._active:
            return
        if self._hides_during_playback():
            self.hide()
            self._ghost_layer.hide()
            return
        self._set_input_transparent(False)
        # Same origin + size as the embed. Mapping both corners added 1px and
        # recentering then ate mpv's asymmetric ml/mr (right-only drift).
        top_left = host.mapToGlobal(QPoint(0, 0))
        self.setGeometry(QRect(top_left, host.size()))

        if content_rect is not None and content_rect.width() > 1 and content_rect.height() > 1:
            content = QRectF(content_rect)
        else:
            content = _aspect_content_rect(
                float(max(1, host.width())),
                float(max(1, host.height())),
                self._content_aspect,
            )
        self._content = content
        self._ghost_layer.set_content_rect(content)

        self._refresh_mask()
        self.show()
        self.raise_()
        self._ghost_layer.layout_on_host(host)
        self.raise_()
        self.update()

    def _refresh_mask(self) -> None:
        """Only the active box receives mouse; captions and empty host stay click-through."""
        region = QRegion()
        if self._chrome.isVisible():
            region = QRegion(self._chrome.geometry())
        if self._interactive:
            rect = _norm_to_rect(self._content, self._box).adjusted(-4, -4, 4, 4)
            region = region.united(QRegion(rect.toRect()))
            for handle in self._handle_rects(_norm_to_rect(self._content, self._box)).values():
                region = region.united(QRegion(handle.toRect().adjusted(-1, -1, 1, 1)))
        hole = self._caption_hit_local()
        if not hole.isEmpty():
            region = region.subtracted(QRegion(hole))
        self.setMask(region)

    def _caption_hit_local(self) -> QRect:
        if self._caption_hit.isEmpty():
            return QRect()
        return QRect(self.mapFromGlobal(self._caption_hit.topLeft()), self._caption_hit.size())

    def _set_keyframe_index(self, index: int, *, emit: bool) -> None:
        if self._keyframe_count <= 1:
            index = 0
        index = max(0, min(int(index), max(0, self._keyframe_count - 1)))
        self._keyframe_index = index
        self._refresh_keyframe_chrome()
        self.update()
        if emit and not self._syncing:
            self.keyframe_index_changed.emit(index)
            self.endpoint_changed.emit(self.endpoint)

    def _set_endpoint(self, endpoint: str, *, emit: bool) -> None:
        if endpoint == "end" and self._keyframe_count > 1:
            index = max(0, self._keyframe_count - 1)
        else:
            index = 0
        self._set_keyframe_index(index, emit=emit)

    def _refresh_keyframe_chrome(self) -> None:
        count = max(1, self._keyframe_count)
        index = 0 if count <= 1 else self._keyframe_index
        offsets = self._keyframe_offsets or [0.0]
        if len(offsets) < count:
            offsets = [*offsets, *[1.0] * (count - len(offsets))]
        self._pose_strip.set_poses(offsets[:count], index)
        self._add_btn.setEnabled(True)
        self._del_btn.setEnabled(count > 1)

    def _handle_rects(self, rect: QRectF) -> dict[str, QRectF]:
        h = _HANDLE
        return {
            "resize_tl": QRectF(rect.left() - 2, rect.top() - 2, h, h),
            "resize_tr": QRectF(rect.right() - h + 2, rect.top() - 2, h, h),
            "resize_bl": QRectF(rect.left() - 2, rect.bottom() - h + 2, h, h),
            "resize_br": QRectF(rect.right() - h + 2, rect.bottom() - h + 2, h, h),
        }

    def _hit_test(self, pos: QPointF) -> str | None:
        if not self._interactive:
            return None
        if self._chrome.geometry().contains(pos.toPoint()):
            return None
        hole = self._caption_hit_local()
        if not hole.isEmpty() and hole.contains(pos.toPoint()):
            return None
        rect = _norm_to_rect(self._content, self._box)
        for mode, handle in self._handle_rects(rect).items():
            if handle.contains(pos):
                return mode
        if rect.contains(pos):
            return "move"
        return None

    def _cursor_for_mode(self, mode: str | None) -> Qt.CursorShape:
        if mode in {"resize_tl", "resize_br"}:
            return Qt.CursorShape.SizeFDiagCursor
        if mode in {"resize_tr", "resize_bl"}:
            return Qt.CursorShape.SizeBDiagCursor
        if mode == "move":
            return Qt.CursorShape.SizeAllCursor
        return Qt.CursorShape.ArrowCursor

    def paintEvent(self, event) -> None:  # noqa: N802
        if not self._active:
            return
        painter = QPainter(self)
        painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceOver)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = _paint_normalized_box(
            painter,
            self._content,
            self._box,
            fill=QColor(40, 140, 220, 55),
            edge=QColor(0, 220, 255, 255),
            overflow_edge=QColor(0, 220, 255, 120),
        )
        if self._interactive:
            painter.setBrush(QColor(255, 255, 255, 245))
            painter.setPen(QPen(QColor(0, 160, 220, 255), 2))
            for handle in self._handle_rects(rect).values():
                painter.drawRect(handle)

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if not self._active or not self._interactive:
            event.ignore()
            return
        if event.button() != Qt.MouseButton.LeftButton:
            event.ignore()
            return
        pos = event.position() if hasattr(event, "position") else QPointF(event.pos())
        if self._chrome.geometry().contains(pos.toPoint()):
            event.ignore()
            return
        hole = self._caption_hit_local()
        if not hole.isEmpty() and hole.contains(pos.toPoint()):
            event.ignore()
            return
        mode = self._hit_test(pos)
        if mode is None:
            event.ignore()
            return
        self._drag_mode = mode
        self._press_global = QPointF(
            event.globalPosition() if hasattr(event, "globalPosition") else event.globalPos()
        )
        self._press_box = dict(self._box)
        self.grabMouse()
        self.setCursor(self._cursor_for_mode(mode))
        self.drag_active_changed.emit(True)
        event.accept()

    def mouseDoubleClickEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            global_pos = (
                event.globalPosition().toPoint()
                if hasattr(event, "globalPosition")
                else event.globalPos()
            )
            self.double_clicked.emit(global_pos)
            hole = self._caption_hit_local()
            pos = event.position() if hasattr(event, "position") else QPointF(event.pos())
            if not hole.isEmpty() and hole.contains(pos.toPoint()):
                self._release_drag()
                event.accept()
                return
        super().mouseDoubleClickEvent(event)

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        pos = event.position() if hasattr(event, "position") else QPointF(event.pos())
        if self._drag_mode is None:
            self.setCursor(self._cursor_for_mode(self._hit_test(pos)))
            return
        if self._press_global is None or self._press_box is None or self._drag_mode is None:
            return
        global_pos = QPointF(
            event.globalPosition() if hasattr(event, "globalPosition") else event.globalPos()
        )
        content = self._content
        dx = (global_pos.x() - self._press_global.x()) / max(1.0, content.width())
        dy = (global_pos.y() - self._press_global.y()) / max(1.0, content.height())
        self._box = apply_overlay_drag(
            self._press_box,
            self._drag_mode,
            dx,
            dy,
            lock_source_aspect=self._lock_source_aspect,
        )
        self._ghost_layer.set_boxes(self._ghosts, self._box, path=self._path)
        self._refresh_mask()
        self.update()
        self.box_changed.emit(dict(self._box))
        event.accept()

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        if self._drag_mode is not None:
            self.box_changed.emit(dict(self._box))
            self._refresh_mask()
        self._release_drag()
        event.accept()

    def _release_drag(self) -> None:
        was_dragging = self._drag_mode is not None
        if self.mouseGrabber() is self:
            self.releaseMouse()
        self._drag_mode = None
        self._press_global = None
        self._press_box = None
        self.setCursor(Qt.CursorShape.ArrowCursor)
        if was_dragging:
            self.drag_active_changed.emit(False)
