"""libmpv embed backend for Phase 3 preview."""

from __future__ import annotations

import contextlib
import importlib.util
import locale
import logging
import sys
from pathlib import Path
from typing import Any

from PySide6.QtCore import QEvent, QObject, Qt, QTimer, Signal
from PySide6.QtWidgets import QLabel, QSizePolicy, QVBoxLayout, QWidget

from corrcut.preview.playback_speed import clamp_playback_rate

logger = logging.getLogger(__name__)

# mpv soft-volume property is 0–100; gain ≈ (volume / 100)³ (not linear amplitude).
MPV_VOLUME_MIN = 0
MPV_VOLUME_MAX = 100


def aspect_letterbox_rect(
    host_w: float, host_h: float, aspect: float | None
) -> tuple[float, float, float, float] | None:
    """Letterbox (x, y, w, h) for a picture of the given aspect inside the host."""
    if aspect is None or aspect <= 0.01 or host_w <= 1 or host_h <= 1:
        return None
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
    return (x, y, content_w, content_h)


def _mpv_get(mpv: Any, name: str) -> Any:
    """Read a libmpv *property*. ``mpv[name]`` is options-only in python-mpv."""
    if mpv is None:
        return None
    getter = getattr(mpv, "_get_property", None)
    if callable(getter):
        try:
            return getter(name)
        except Exception:
            return None
    try:
        return mpv[name]
    except Exception:
        return None


def _mpv_float(mpv: Any, name: str) -> float | None:
    raw = _mpv_get(mpv, name)
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return None
    return value


def video_letterbox_rect(
    mpv: Any,
    host_w: float,
    host_h: float,
    *,
    fallback_aspect: float | None = None,
    device_pixel_ratio: float = 1.0,
) -> tuple[float, float, float, float] | None:
    """Return (x, y, w, h) of the video picture inside the embed, in host coords.

    Prefer mpv OSD picture margins (what is actually drawn). Fall back to
    display/storage aspect letterboxed into the host.
    """
    if host_w <= 1 or host_h <= 1:
        return None
    result = _osd_picture_in_host(mpv, host_w, host_h, device_pixel_ratio)
    if result is None:
        aspect = _mpv_picture_aspect(mpv)
        if aspect is None or aspect <= 0.01:
            aspect = fallback_aspect
        result = aspect_letterbox_rect(host_w, host_h, aspect)
    return result


def _osd_picture_in_host(
    mpv: Any,
    host_w: float,
    host_h: float,
    dpr: float,
) -> tuple[float, float, float, float] | None:
    osd_w = _mpv_float(mpv, "osd-dimensions/w")
    osd_h = _mpv_float(mpv, "osd-dimensions/h")
    ml = _mpv_float(mpv, "osd-dimensions/ml")
    mr = _mpv_float(mpv, "osd-dimensions/mr")
    mt = _mpv_float(mpv, "osd-dimensions/mt")
    mb = _mpv_float(mpv, "osd-dimensions/mb")
    if osd_w is None or osd_h is None or osd_w < 2.0 or osd_h < 2.0:
        return None
    if None in (ml, mr, mt, mb):
        return None
    pic_w = osd_w - ml - mr
    pic_h = osd_h - mt - mb
    if pic_w < 2.0 or pic_h < 2.0:
        return None
    scale_dpr = float(dpr) if dpr and dpr > 0 else 1.0
    expected_w = host_w * scale_dpr
    expected_h = host_h * scale_dpr
    if abs(osd_w - expected_w) <= 2.0 and abs(osd_h - expected_h) <= 2.0:
        scale = 1.0 / scale_dpr
        return (ml * scale, mt * scale, pic_w * scale, pic_h * scale)
    sx = host_w / osd_w
    sy = host_h / osd_h
    if abs(sx - sy) > 0.02 * max(sx, sy):
        return aspect_letterbox_rect(host_w, host_h, pic_w / pic_h)
    scale = (sx + sy) / 2.0
    return (ml * scale, mt * scale, pic_w * scale, pic_h * scale)


def _mpv_picture_aspect(mpv: Any) -> float | None:
    """Aspect of the video picture as shown in the VO (matches cover on screen)."""
    if mpv is None:
        return None
    osd_w = _mpv_float(mpv, "osd-dimensions/w")
    osd_h = _mpv_float(mpv, "osd-dimensions/h")
    ml = _mpv_float(mpv, "osd-dimensions/ml") or 0.0
    mr = _mpv_float(mpv, "osd-dimensions/mr") or 0.0
    mt = _mpv_float(mpv, "osd-dimensions/mt") or 0.0
    mb = _mpv_float(mpv, "osd-dimensions/mb") or 0.0
    if osd_w and osd_h and osd_w > 2.0 and osd_h > 2.0:
        pic_w = osd_w - ml - mr
        pic_h = osd_h - mt - mb
        if pic_w >= 2.0 and pic_h >= 2.0 and (ml + mr + mt + mb) > 1.0:
            return pic_w / pic_h
    for key in (
        "video-out-params/aspect",
        "video-params/aspect",
        "dwidth",
    ):
        if key == "dwidth":
            dw = _mpv_float(mpv, "dwidth")
            dh = _mpv_float(mpv, "dheight")
            if dw and dh and dw > 1.0 and dh > 1.0:
                return dw / dh
            continue
        aspect = _mpv_float(mpv, key)
        if aspect is not None and aspect > 0.01:
            return aspect
    for prefix in ("video-out-params", "video-params"):
        w = _mpv_float(mpv, f"{prefix}/w")
        h = _mpv_float(mpv, f"{prefix}/h")
        if w and h and w > 1.0 and h > 1.0:
            return w / h
    return None


def linear_percent_to_mpv_volume(linear_percent: int) -> float:
    """Map UI slider (linear amplitude 0–100) to mpv's cubic soft-volume property."""
    linear = max(0, min(100, int(linear_percent)))
    if linear == 0:
        return 0.0
    gain = linear / 100.0
    return 100.0 * (gain ** (1.0 / 3.0))


def mpv_volume_to_linear_percent(mpv_vol: float) -> int:
    """Map mpv soft-volume back to a linear-amplitude slider position."""
    vol = max(0.0, min(float(MPV_VOLUME_MAX), float(mpv_vol)))
    if vol == 0.0:
        return 0
    gain = (vol / 100.0) ** 3
    return int(round(gain * 100.0))


def _prepare_bindings() -> bool:
    from corrcut.config import AppPaths
    from corrcut.preview.libmpv_loader import prepare_libmpv

    return prepare_libmpv(AppPaths.default())


def is_mpv_available() -> bool:
    if importlib.util.find_spec("mpv") is None:
        return False
    return _prepare_bindings()


def _mpv_track_ffmpeg_index(track: dict[str, Any]) -> int | None:
    """FFmpeg stream index for an mpv audio track, if the property is present."""
    for key in ("ff_index", "ff-index", "demux_id", "demux-id"):
        raw = track.get(key)
        if raw is None:
            continue
        try:
            return int(raw)
        except (TypeError, ValueError):
            continue
    return None


class MpvPlayerBackend(QObject):
    """Thin wrapper around python-mpv for file playback."""

    position_changed = Signal(float)
    duration_changed = Signal(float)
    playback_state_changed = Signal(bool)
    tracks_ready = Signal()

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._mpv: Any = None
        self._duration = 0.0
        self._poll = QTimer(self)
        self._poll.setInterval(100)
        self._poll.timeout.connect(self._poll_position)
        self._was_playing = False
        self._subtitles_visible = True
        self._pending_load_path: Path | None = None
        self._selected_aid: int | None = None
        self._selected_vid: int | None = None
        self._audio_tracks_cache: list[dict[str, Any]] | None = None
        self._cached_time_pos = 0.0
        self._playback_rate = 1.0
        self._reverse_warned = False

    def create(self, win_id: int) -> None:
        if self._mpv is not None:
            return
        locale.setlocale(locale.LC_NUMERIC, "C")
        if not _prepare_bindings():
            raise OSError("libmpv is not available")
        import mpv

        mpv_kwargs: dict[str, object] = {
            "wid": str(win_id),
            "vo": "gpu",
            # Software decode: video lavfi (cover/blank) seeks reliably on Windows.
            "hwdec": "no",
            "keep_open": "yes",
            "idle": "yes",
            "input_default_bindings": False,
            "input_vo_keyboard": False,
            "volume": 100,
            "mute": False,
            # Faster open on large MKV: limit libavformat probe, demux on a worker thread.
            "demuxer_thread": "yes",
            "demuxer_lavf_analyzeduration": "0.5",
            "demuxer_lavf_probesize": "65536",
        }
        # Let mpv auto-pick the best audio backend first.
        if sys.platform == "darwin":
            mpv_kwargs["ao"] = "coreaudio"

        self._mpv = mpv.MPV(**mpv_kwargs)
        self._mpv.observe_property("time-pos", self._on_time_pos)
        self._mpv.observe_property("duration", self._on_duration)
        self._mpv.observe_property("pause", self._on_pause)
        self._mpv.observe_property("track-list", self._on_track_list)
        if self._pending_load_path is not None:
            pending = self._pending_load_path
            self._pending_load_path = None
            self.load(pending)

    def shutdown(self) -> None:
        self._poll.stop()
        self._selected_aid = None
        self._selected_vid = None
        if self._mpv is not None:
            self._mpv.terminate()
            self._mpv = None

    def suspend_for_modal(self) -> None:
        """Terminate libmpv so native Windows file dialogs cannot deadlock on the embed."""
        self._poll.stop()
        self._pending_load_path = None
        self._selected_aid = None
        self._selected_vid = None
        if self._mpv is not None:
            try:
                self._mpv.pause = True
                self._mpv.lavfi_complex = ""
            except Exception:
                logger.debug("mpv suspend prep failed", exc_info=True)
            try:
                self._mpv.terminate()
            except Exception:
                logger.debug("mpv terminate failed", exc_info=True)
            self._mpv = None

    def load(self, path: Path) -> None:
        if self._mpv is None:
            self._pending_load_path = path
            return
        self._audio_tracks_cache = None
        self._selected_vid = None
        self._cached_time_pos = 0.0
        self._mpv.command_async("loadfile", str(path.resolve()), "replace")
        self._mpv.pause = True
        # loadfile resets speed and direction back to the defaults.
        self._apply_playback_rate()
        # Subtitles are applied after media is ready (sid=auto can block ~10s on large files).

    def ingest_track_list(self, track_list: Any) -> None:
        """Update cached audio tracks (tests and mpv track-list observer)."""
        had_tracks = bool(self._audio_tracks_cache)
        if not track_list:
            self._audio_tracks_cache = []
            return
        self._audio_tracks_cache = sorted(
            (t for t in track_list if isinstance(t, dict) and t.get("type") == "audio"),
            key=lambda t: int(t["id"]),
        )
        if self._selected_vid is None:
            videos = [t for t in track_list if isinstance(t, dict) and t.get("type") == "video"]
            selected = next((t for t in videos if t.get("selected")), None)
            pick = selected or (videos[0] if videos else None)
            if pick is not None:
                with contextlib.suppress(KeyError, TypeError, ValueError):
                    self._selected_vid = int(pick["id"])
        if self._audio_tracks_cache and not had_tracks:
            self.tracks_ready.emit()

    def _on_track_list(self, _name: str, value: Any) -> None:
        self.ingest_track_list(value)

    def set_subtitles_visible(self, visible: bool, *, sid: str | None = None) -> None:
        """Show or hide embedded/external subtitles in the video preview."""
        self._subtitles_visible = visible
        if self._mpv is None:
            return
        sid_value = sid if sid is not None else "auto" if visible else "no"
        try:
            self._mpv.command_async("set", "sub-visibility", "yes" if visible else "no")
            self._mpv.command_async("set", "sid", sid_value)
        except Exception:
            self._mpv.sub_visibility = visible
            self._mpv.sid = sid_value

    def has_audio_tracks(self) -> bool:
        return bool(self._audio_tracks_cache)

    @property
    def selected_audio_id(self) -> int | None:
        """mpv aid last chosen by ``set_audio_stream``. Lavfi pads must follow it."""
        return self._selected_aid

    def _resolve_audio_track_id(
        self,
        ffmpeg_stream_index: int,
        *,
        audio_ordinal: int,
    ) -> int | None:
        audio_tracks = self._audio_tracks_cache
        if not audio_tracks:
            return None
        for track in audio_tracks:
            src_index = _mpv_track_ffmpeg_index(track)
            if src_index is not None and src_index == ffmpeg_stream_index:
                return int(track["id"])
        idx = min(max(0, audio_ordinal), len(audio_tracks) - 1)
        return int(audio_tracks[idx]["id"])

    def set_audio_stream(self, ffmpeg_stream_index: int, *, audio_ordinal: int = 0) -> bool:
        if self._mpv is None:
            return False
        # Never read mpv.track_list synchronously — on large files it can block the UI ~10s+.
        target_id = self._resolve_audio_track_id(
            ffmpeg_stream_index,
            audio_ordinal=audio_ordinal,
        )
        if target_id is None:
            return False
        if self._selected_aid == target_id:
            return True
        try:
            self._mpv.command_async("set", "aid", str(target_id))
        except Exception:
            try:
                self._mpv.aid = target_id
            except Exception:
                logger.debug("mpv aid assignment failed (target=%s)", target_id, exc_info=True)
                return False
        self._selected_aid = target_id
        logger.debug(
            "mpv aid=%s (ffmpeg stream %s, ordinal %s)",
            target_id,
            ffmpeg_stream_index,
            audio_ordinal,
        )
        return True

    def set_lavfi_complex(self, graph: str | None) -> None:
        if self._mpv is None:
            return
        # Empty clears the graph; otherwise bind input pads to selected tracks.
        # mpv requires track-id-suffixed pad names (e.g. [aid1]/[vid1]); bare
        # [aid]/[vid] labels silently collapse output.
        value = graph if graph else ""
        if value:
            # Prefer last explicitly-selected track ids: once a graph is active
            # mpv reports aid/vid as False, which would corrupt labels to [aid0].
            aid_int = self._selected_aid
            if aid_int is None:
                live = getattr(self._mpv, "aid", None)
                if isinstance(live, int) and not isinstance(live, bool):
                    aid_int = live
            if aid_int is not None:
                value = value.replace("[aid]", f"[aid{aid_int}]")
            vid_int = self._selected_vid
            if vid_int is None:
                live_v = getattr(self._mpv, "vid", None)
                if isinstance(live_v, int) and not isinstance(live_v, bool):
                    vid_int = live_v
                    self._selected_vid = vid_int
            if vid_int is not None:
                value = value.replace("[vid]", f"[vid{vid_int}]")
        try:
            # Prefer synchronous set so filter parse errors surface as Python
            # exceptions instead of async native faults where possible.
            self._mpv.lavfi_complex = value
        except Exception:
            logger.exception("Failed setting lavfi-complex (%s chars)", len(value))
            try:
                self._mpv.lavfi_complex = ""
            except Exception:
                logger.debug("Failed clearing lavfi-complex after error", exc_info=True)

    def audio_devices(self) -> list[tuple[str, str]]:
        """Return [(name, description)] for mpv audio outputs."""
        if self._mpv is None:
            return []
        devices = getattr(self._mpv, "audio_device_list", None) or []
        rows: list[tuple[str, str]] = []
        for item in devices:
            if not isinstance(item, dict):
                continue
            name = str(item.get("name") or "")
            if not name:
                continue
            desc = str(item.get("description") or name)
            rows.append((name, desc))
        return rows

    def current_audio_device(self) -> str:
        if self._mpv is None:
            return ""
        value = getattr(self._mpv, "audio_device", "") or ""
        return str(value)

    def set_audio_device(self, device_name: str) -> bool:
        if self._mpv is None:
            return False
        try:
            self._mpv.audio_device = device_name
        except Exception:
            logger.exception("Failed setting mpv audio_device=%s", device_name)
            return False
        return True

    def volume(self) -> int:
        """Return linear-amplitude volume for the UI slider (0–100)."""
        if self._mpv is None:
            return 100
        try:
            value = getattr(self._mpv, "volume", 100)
            return mpv_volume_to_linear_percent(float(value))
        except Exception:
            return 100

    def set_volume(self, linear_percent: int) -> bool:
        """Set preview volume; ``linear_percent`` is linear amplitude (0=mute, 100=full)."""
        if self._mpv is None:
            return False
        vol = linear_percent_to_mpv_volume(linear_percent)
        try:
            self._mpv.volume = vol
        except Exception:
            logger.exception("Failed setting mpv volume=%s (linear=%s)", vol, linear_percent)
            return False
        return True

    def is_muted(self) -> bool:
        if self._mpv is None:
            return False
        try:
            return bool(getattr(self._mpv, "mute", False))
        except Exception:
            return False

    def playback_rate(self) -> float:
        return self._playback_rate

    def set_playback_rate(self, rate: float) -> float:
        """Set signed preview rate (negative plays backward). Returns the applied rate."""
        wanted = clamp_playback_rate(rate)
        if self._mpv is None:
            self._playback_rate = wanted
            return wanted
        previous = self._playback_rate
        if not self._apply_rate(wanted):
            return previous
        self._playback_rate = wanted
        return wanted

    def _apply_playback_rate(self) -> None:
        if self._mpv is None:
            return
        self._apply_rate(self._playback_rate)

    def _apply_rate(self, rate: float) -> bool:
        if self._mpv is None:
            return False
        speed = abs(rate)
        try:
            self._mpv.speed = speed
        except Exception:
            logger.exception("Failed setting mpv speed=%s", speed)
            return False
        direction = "backward" if rate < 0.0 else "forward"
        if self._set_play_direction(direction):
            return True
        if rate < 0.0:
            try:
                self._mpv.speed = abs(self._playback_rate) if self._playback_rate else 1.0
                self._set_play_direction("forward")
            except Exception:
                logger.debug("mpv speed restore after reverse failure failed", exc_info=True)
            return False
        return True

    def _set_play_direction(self, direction: str) -> bool:
        if self._mpv is None:
            return False
        try:
            self._mpv.play_direction = direction
            return True
        except Exception:
            if direction == "backward" and not self._reverse_warned:
                self._reverse_warned = True
                logger.warning(
                    "mpv play-direction=backward is unavailable; reverse playback disabled"
                )
            else:
                logger.debug("mpv play-direction=%s failed", direction, exc_info=True)
            return False

    def set_muted(self, muted: bool) -> bool:
        if self._mpv is None:
            return False
        try:
            self._mpv.mute = bool(muted)
        except Exception:
            logger.exception("Failed setting mpv mute=%s", muted)
            return False
        return True

    def seek(self, seconds: float, *, reference: str = "absolute") -> None:
        if self._mpv is None:
            return
        t = max(0.0, float(seconds))
        self._cached_time_pos = t
        try:
            self._mpv.command_async("seek", t, reference)
        except Exception:
            try:
                self._mpv.command("seek", t, reference)
            except Exception:
                # mpv can reject a seek while a load/rebuild is settling; non-fatal.
                logger.debug("mpv seek failed (seconds=%s, reference=%s)", seconds, reference)
                return

    def play(self) -> None:
        if self._mpv is None:
            return
        self._mpv.pause = False
        self._poll.start()

    def pause(self) -> None:
        if self._mpv is None:
            return
        self._mpv.pause = True
        self._poll.stop()

    def toggle_playback(self) -> bool:
        if self._mpv is None:
            return False
        playing = not bool(self._mpv.pause)
        if playing:
            self.pause()
            return False
        self.play()
        return True

    def stop(self) -> None:
        if self._mpv is None:
            return
        self.pause()
        try:
            self._mpv.command_async("seek", 0, "absolute")
        except Exception:
            logger.debug("mpv async seek-to-start failed", exc_info=True)

    def unload_media(self) -> None:
        """Stop playback and detach the current file (non-blocking; safe before modal dialogs)."""
        if self._mpv is None:
            self._pending_load_path = None
            return
        self._audio_tracks_cache = None
        self._selected_vid = None
        self._cached_time_pos = 0.0
        self._poll.stop()
        self._pending_load_path = None
        try:
            self._mpv.pause = True
            self._mpv.lavfi_complex = ""
            # Never use synchronous command() here — deadlocks embedded mpv on Windows.
            self._mpv.command_async("stop")
        except Exception:
            logger.debug("mpv unload_media failed", exc_info=True)

    def frame_step(self, delta: int) -> None:
        if self._mpv is None or delta == 0:
            return
        cmd = "frame-step" if delta > 0 else "frame-back-step"
        for _ in range(abs(delta)):
            try:
                self._mpv.command_async(cmd)
            except Exception:
                self._mpv.command(cmd)

    def position(self) -> float:
        if self._mpv is None:
            return 0.0
        return float(self._cached_time_pos)

    def duration(self) -> float:
        return self._duration

    def video_letterbox_rect(
        self,
        host_w: float,
        host_h: float,
        *,
        fallback_aspect: float | None = None,
        device_pixel_ratio: float = 1.0,
    ) -> tuple[float, float, float, float] | None:
        """Video picture rect inside the embed widget (letterbox-aware)."""
        return video_letterbox_rect(
            self._mpv,
            host_w,
            host_h,
            fallback_aspect=fallback_aspect,
            device_pixel_ratio=device_pixel_ratio,
        )

    def is_playing(self) -> bool:
        if self._mpv is None:
            return False
        return not bool(self._mpv.pause)

    def _on_time_pos(self, _name: str, value: float | None) -> None:
        if value is None:
            return
        self._cached_time_pos = float(value)
        self.position_changed.emit(self._cached_time_pos)

    def _on_duration(self, _name: str, value: float | None) -> None:
        if value is None or value <= 0:
            return
        self._duration = float(value)
        self.duration_changed.emit(self._duration)

    def _on_pause(self, _name: str, value: bool | None) -> None:
        if value is None:
            return
        playing = not bool(value)
        self.playback_state_changed.emit(playing)

    def _poll_position(self) -> None:
        if self._mpv is None:
            return
        self.position_changed.emit(self.position())


class MpvEmbedWidget(QWidget):
    """Native-window mpv embed; must be shown before create()."""

    native_window_ready = Signal()

    def suspend_for_modal(self) -> None:
        self.backend.suspend_for_modal()

    def resume_after_modal(self) -> None:
        if self.backend._mpv is None and is_mpv_available():
            self.backend.create(int(self.winId()))

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        # Qt.WA_* works across PySide6 builds (avoid Qt.WidgetAttribute / QWidget.WidgetAttribute).
        self.setAttribute(Qt.WA_DontCreateNativeAncestors, True)
        self.setAttribute(Qt.WA_NativeWindow, True)
        self.setMinimumHeight(240)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.backend = MpvPlayerBackend(self)

    def showEvent(self, event: QEvent) -> None:  # noqa: N802
        super().showEvent(event)
        if is_mpv_available() and self.backend._mpv is None:
            self.backend.create(int(self.winId()))
        if is_mpv_available():
            # mpv's native HWND is created asynchronously; re-emit after the event loop
            # so sibling overlay controls can stack above the video surface.
            QTimer.singleShot(0, self.native_window_ready.emit)

    def resizeEvent(self, event: QEvent) -> None:  # noqa: N802
        super().resizeEvent(event)
        if is_mpv_available():
            QTimer.singleShot(0, self.native_window_ready.emit)

    def closeEvent(self, event: QEvent) -> None:  # noqa: N802
        self.backend.shutdown()
        super().closeEvent(event)


class MpvUnavailableWidget(QWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        from corrcut.config import AppPaths
        from corrcut.preview.libmpv_loader import libmpv_status_message

        hint = libmpv_status_message(AppPaths.default())
        label = QLabel(f"<b>Video preview unavailable</b><br><br>{hint}")
        label.setWordWrap(True)
        label.setOpenExternalLinks(True)
        label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(label)
