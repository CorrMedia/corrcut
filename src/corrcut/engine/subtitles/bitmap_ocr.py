"""Convert bitmap (PGS) subtitle streams to text via OCR."""

from __future__ import annotations

import tempfile
from collections.abc import Callable
from fractions import Fraction
from pathlib import Path

from corrcut.ai.subtitles import (
    SubtitleCue,
    extract_subtitle_sup,
    extract_subtitle_vobsub,
    is_dvd_subtitle_codec,
    is_ocr_subtitle_codec,
    select_bitmap_subtitle_stream_index,
)
from corrcut.engine.subtitles.ocr_cleanup import clean_ocr_cues

_VOBSUB_LANG_BY_ISO: dict[str, str] = {
    "en": "eng",
    "eng": "eng",
    "es": "spa",
    "spa": "spa",
    "fr": "fra",
    "fra": "fra",
    "fre": "fra",
    "de": "deu",
    "deu": "deu",
    "ger": "deu",
    "it": "ita",
    "ita": "ita",
    "pt": "por",
    "por": "por",
    "ru": "rus",
    "rus": "rus",
    "ja": "jpn",
    "jpn": "jpn",
    "zh": "chi_sim",
    "zho": "chi_sim",
    "chi": "chi_sim",
    "ko": "kor",
    "kor": "kor",
    "nl": "nld",
    "nld": "nld",
    "dut": "nld",
    "sv": "swe",
    "swe": "swe",
    "no": "nor",
    "nor": "nor",
    "da": "dan",
    "dan": "dan",
    "fi": "fin",
    "fin": "fin",
}

_DEFAULT_DURATION_MS = 3_000.0
_MIN_DURATION_MS = 100.0


def ocr_install_hint() -> str:
    from corrcut.engine.subtitles.paddle_ocr_engine import (
        paddle_install_hint,
        paddle_unavailable_reason,
    )

    return paddle_unavailable_reason() or paddle_install_hint()


def is_pgs_ocr_available() -> bool:
    from corrcut.engine.subtitles.paddle_ocr_engine import is_paddleocr_available

    return is_paddleocr_available()


def is_ocr_available() -> bool:
    return is_pgs_ocr_available()


def vobsub_language_code(raw: str | None) -> str:
    if raw is None:
        return "eng"
    key = raw.strip().lower().replace("_", "-")
    if key in _VOBSUB_LANG_BY_ISO:
        return _VOBSUB_LANG_BY_ISO[key]
    if len(key) >= 3:
        return _VOBSUB_LANG_BY_ISO.get(key[:3], key[:3])
    return _VOBSUB_LANG_BY_ISO.get(key, "eng")


def tesseract_language_code(raw: str | None) -> str:
    """Backward-compatible alias for VobSub OCR language codes."""
    return vobsub_language_code(raw)


def _resolve_pgs_language(
    *,
    language_hint: str | None,
    stream_language: str | None,
    audio_language: str | None,
) -> str:
    from corrcut.engine.subtitles.paddle_ocr_engine import paddle_language_code

    for raw in (language_hint, stream_language, audio_language, "eng"):
        if raw and str(raw).strip():
            return paddle_language_code(str(raw))
    return "en"


def _paddle_running_message(lang: str) -> str:
    from corrcut.engine.subtitles.paddle_ocr_engine import paddle_device_label

    return f"Running PaddleOCR ({lang}, {paddle_device_label()})…"


def _ocr_images_in_batches(
    images: list[object],
    *,
    lang: str,
    progress: Callable[[int, str], None] | None,
    cancel_event: object | None,
    total_events: int,
    event_indexes: list[int] | None = None,
) -> list[str]:
    """OCR images in chunks so cancel and progress run between GPU batches."""
    from corrcut.engine.jobs.cancel import check_cancelled  # noqa: PLC0415
    from corrcut.engine.subtitles.paddle_ocr_engine import (  # noqa: PLC0415
        _OCR_BATCH_SIZE,
        ocr_rgba_images,
    )

    texts: list[str] = []
    image_count = len(images)
    if image_count == 0:
        return texts
    for start in range(0, image_count, _OCR_BATCH_SIZE):
        check_cancelled(cancel_event)  # type: ignore[arg-type]
        chunk = images[start : start + _OCR_BATCH_SIZE]
        done = min(start + len(chunk), image_count)
        if progress is not None:
            if event_indexes is not None:
                shown = event_indexes[done - 1] + 1
            else:
                shown = min(done, total_events)
            percent = 20 + int((done / max(image_count, 1)) * 75)
            progress(percent, f"OCR subtitle {shown}/{total_events}…")
        texts.extend(ocr_rgba_images(chunk, lang=lang))
    return texts


def _cue_times(start_ms: float, end_ms: float | None) -> tuple[float, float]:
    if end_ms is None:
        end_ms = start_ms + _DEFAULT_DURATION_MS
    if end_ms - start_ms < _MIN_DURATION_MS:
        end_ms = start_ms + _MIN_DURATION_MS
    return start_ms, end_ms


def _is_erase_event(ds: object) -> bool:
    pcs = getattr(ds, "pcs", None)
    if pcs is None:
        return True
    return len(getattr(pcs, "objects", [])) == 0


def _convert_sup_path_to_cues(
    sup_path: Path,
    *,
    lang: str,
    progress: Callable[[int, str], None] | None = None,
    cancel_event: object | None = None,
) -> list[SubtitleCue]:
    from sup2srt.sup_decoder import decode_display_set  # noqa: PLC0415
    from sup2srt.sup_parser import SupParser  # noqa: PLC0415

    display_sets = SupParser(sup_path).parse()
    if not display_sets:
        raise RuntimeError("PGS file contained no subtitle events")

    subtitle_events: list[tuple[float, float | None, object]] = []
    pending: tuple[float, object] | None = None
    for ds in display_sets:
        pcs = getattr(ds, "pcs", None)
        if pcs is None:
            continue
        pts_ms = float(pcs.pts_ms)
        if _is_erase_event(ds):
            if pending is not None:
                subtitle_events.append((pending[0], pts_ms, pending[1]))
                pending = None
        else:
            if pending is not None:
                subtitle_events.append((pending[0], None, pending[1]))
            pending = (pts_ms, ds)
    if pending is not None:
        subtitle_events.append((pending[0], None, pending[1]))

    if not subtitle_events:
        raise RuntimeError("PGS file contained no OCR-ready subtitle events")

    decoded_events: list[tuple[float, float | None, list[object]]] = []
    flat_images: list[object] = []
    flat_owners: list[int] = []
    for index, (start_ms, end_ms, ds) in enumerate(subtitle_events):
        decoded = decode_display_set(ds)
        images: list[object] = []
        if decoded:
            sorted_images = sorted(decoded, key=lambda item: int(getattr(item, "y", 0)))
            images = [item.image for item in sorted_images]
        decoded_events.append((start_ms, end_ms, images))
        for image in images:
            flat_images.append(image)
            flat_owners.append(index)

    ocr_texts = _ocr_images_in_batches(
        flat_images,
        lang=lang,
        progress=progress,
        cancel_event=cancel_event,
        total_events=len(subtitle_events),
        event_indexes=flat_owners,
    )
    text_by_event: dict[int, list[str]] = {}
    for owner, text in zip(flat_owners, ocr_texts, strict=True):
        if text:
            text_by_event.setdefault(owner, []).append(text)

    cues: list[SubtitleCue] = []
    skipped_empty = 0
    total = len(subtitle_events)
    for index, (start_ms, end_ms, _images) in enumerate(decoded_events):
        text = "\n".join(text_by_event.get(index, []))
        if not text:
            skipped_empty += 1
            continue
        start_ms, end_ms = _cue_times(start_ms, end_ms)
        cues.append(
            SubtitleCue(
                start=Fraction(int(round(start_ms)), 1000),
                end=Fraction(int(round(end_ms)), 1000),
                text=text,
            )
        )

    if not cues:
        raise RuntimeError(
            "PaddleOCR produced no subtitle lines "
            f"({total} event(s), {skipped_empty} empty after OCR). "
            "Try another subtitle stream or a different OCR language."
        )
    return cues


def _convert_vobsub_to_cues_paddle(
    idx_path: Path,
    *,
    lang: str,
    progress: Callable[[int, str], None] | None = None,
    cancel_event: object | None = None,
) -> list[SubtitleCue]:
    """Decode VobSub bitmaps and OCR them with PaddleOCR (higher quality)."""
    from corrcut.engine.subtitles.vobsub_decoder import decode_vobsub  # noqa: PLC0415

    events = decode_vobsub(idx_path)
    if not events:
        raise RuntimeError("VobSub decoder produced no subtitle events")

    total = len(events)
    texts = _ocr_images_in_batches(
        [event.image for event in events],
        lang=lang,
        progress=progress,
        cancel_event=cancel_event,
        total_events=total,
    )

    cues: list[SubtitleCue] = []
    for index, event in enumerate(events):
        text = texts[index] if index < len(texts) else ""
        if not text:
            continue

        start_ms = event.start_ms
        end_ms = event.end_ms
        if end_ms is None or end_ms <= start_ms:
            next_start = events[index + 1].start_ms if index + 1 < total else None
            end_ms = next_start if next_start else start_ms + _DEFAULT_DURATION_MS
        start_ms, end_ms = _cue_times(start_ms, end_ms)

        cues.append(
            SubtitleCue(
                start=Fraction(int(round(start_ms)), 1000),
                end=Fraction(int(round(end_ms)), 1000),
                text=text,
            )
        )

    if not cues:
        raise RuntimeError("PaddleOCR produced no subtitle lines from VobSub images")
    return cues


def _ocr_vobsub_idx(
    idx_path: Path,
    *,
    paddle_lang: str,
    progress: Callable[[int, str], None] | None = None,
    cancel_event: object | None = None,
) -> list[SubtitleCue]:
    """OCR a VobSub .idx with PaddleOCR."""
    if progress is not None:
        progress(15, _paddle_running_message(paddle_lang))
    return _convert_vobsub_to_cues_paddle(
        idx_path,
        lang=paddle_lang,
        progress=progress,
        cancel_event=cancel_event,
    )


def convert_bitmap_stream_to_cues(
    source_path: Path,
    subtitle_streams: list[dict[str, object]],
    *,
    preferred_stream_index: int | None,
    audio_language: str | None,
    language_hint: str | None = None,
    progress: Callable[[int, str], None] | None = None,
    cancel_event: object | None = None,
) -> tuple[list[SubtitleCue], int]:
    """
    OCR a bitmap subtitle stream (PGS or DVD VobSub) into text cues.

    Returns (cues, stream_index). Raises RuntimeError when OCR is unavailable or fails.
    """
    from corrcut.engine.jobs.cancel import check_cancelled  # noqa: PLC0415
    from corrcut.engine.subtitles.paddle_ocr_engine import paddle_unavailable_reason

    stream_index = select_bitmap_subtitle_stream_index(
        subtitle_streams,
        preferred_index=preferred_stream_index,
        audio_language=audio_language,
        ocr_only=True,
    )
    if stream_index is None:
        raise RuntimeError("No OCR-capable bitmap subtitle stream available")

    stream = next(
        (s for s in subtitle_streams if int(s.get("index", -1)) == stream_index),
        None,
    )
    codec = str(stream.get("codec", "")) if isinstance(stream, dict) else ""
    if not is_ocr_subtitle_codec(codec):
        raise RuntimeError(
            f"Subtitle stream #{stream_index} ({codec}) is not supported for OCR."
        )

    reason = paddle_unavailable_reason()
    if reason:
        raise RuntimeError(reason)

    is_dvd = is_dvd_subtitle_codec(codec)

    stream_language = (
        str(stream.get("language")) if isinstance(stream, dict) and stream.get("language") else None
    )
    paddle_lang = _resolve_pgs_language(
        language_hint=language_hint,
        stream_language=stream_language,
        audio_language=audio_language,
    )
    lang = paddle_lang

    def report(percent: int, message: str) -> None:
        if progress is not None:
            progress(percent, message)

    with tempfile.TemporaryDirectory(prefix="ms_ocr_") as tmp:
        tmp_path = Path(tmp)
        if is_dvd:
            report(5, "Extracting DVD (VobSub) subtitle stream…")
            check_cancelled(cancel_event)  # type: ignore[arg-type]
            idx_path, _sub_path = extract_subtitle_vobsub(
                source_path.resolve(),
                stream_index,
                tmp_path / "extracted",
                subtitle_streams=[s for s in subtitle_streams if isinstance(s, dict)],
            )
            check_cancelled(cancel_event)  # type: ignore[arg-type]
            cues = _ocr_vobsub_idx(
                idx_path,
                paddle_lang=paddle_lang,
                progress=progress,
                cancel_event=cancel_event,
            )
        else:
            report(5, "Extracting PGS subtitle stream…")
            check_cancelled(cancel_event)  # type: ignore[arg-type]
            sup_path = tmp_path / "extracted.sup"
            extract_subtitle_sup(source_path.resolve(), stream_index, sup_path)
            check_cancelled(cancel_event)  # type: ignore[arg-type]

            header = sup_path.read_bytes()[:2]
            if header != b"PG":
                raise RuntimeError(
                    f"Extracted subtitle data is not a valid PGS stream (stream #{stream_index})."
                )

            report(15, _paddle_running_message(lang))
            cues = _convert_sup_path_to_cues(
                sup_path,
                lang=lang,
                progress=progress,
                cancel_event=cancel_event,
            )
        check_cancelled(cancel_event)  # type: ignore[arg-type]

    cues = clean_ocr_cues(cues)
    if not cues:
        raise RuntimeError(
            "OCR produced no subtitle lines after cleanup. "
            "Try another subtitle stream or OCR language."
        )

    report(100, f"OCR complete ({len(cues)} cue(s))")
    return cues, stream_index
