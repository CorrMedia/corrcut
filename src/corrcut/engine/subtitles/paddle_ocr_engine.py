"""PaddleOCR backend for PGS bitmap subtitle recognition."""

from __future__ import annotations

import os
import sys
import threading
from pathlib import Path
from typing import Any

os.environ.setdefault("PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK", "True")

_PADDLE_LANG_BY_TESSERACT: dict[str, str] = {
    "eng": "en",
    "en": "en",
    "spa": "es",
    "es": "es",
    "fra": "fr",
    "fr": "fr",
    "fre": "fr",
    "deu": "german",
    "de": "german",
    "ger": "german",
    "it": "it",
    "ita": "it",
    "pt": "pt",
    "por": "pt",
    "ru": "ru",
    "rus": "ru",
    "ja": "japan",
    "jpn": "japan",
    "ko": "korean",
    "kor": "korean",
    "zh": "ch",
    "chi_sim": "ch",
    "chi_tra": "chinese_cht",
    "nl": "nl",
    "nld": "nl",
    "sv": "sv",
    "swe": "sv",
    "no": "no",
    "nor": "no",
    "da": "da",
    "dan": "da",
    "fi": "fi",
    "fin": "fi",
    "ar": "ar",
    "hi": "hi",
    "th": "th",
    "vi": "vi",
    "pl": "pl",
    "tr": "tr",
    "uk": "uk",
    "cs": "cs",
    "el": "el",
    "latin": "latin",
}

_ENGINE_CACHE: dict[str, object] = {}
_ENGINE_LOCK = threading.Lock()
# Set after a one-time GPU read test. A CUDA build without kernels for this
# GPU detects nothing or emits dictionary noise, so OCR stays on CPU.
_DEVICE_OVERRIDE: str | None = None
_OCR_BATCH_SIZE = 8
_MOBILE_DET_MODEL = "PP-OCRv5_mobile_det"
_EN_REC_MODEL = "en_PP-OCRv5_mobile_rec"
_LATIN_REC_LANGS = frozenset(
    {
        "af", "az", "bs", "cs", "cy", "da", "de", "es", "et", "fr", "ga", "hr",
        "hu", "id", "is", "it", "lt", "lv", "mi", "ms", "mt", "nl", "no", "oc",
        "pi", "pl", "pt", "ro", "rs_latin", "sk", "sl", "sq", "sv", "sw", "tl",
        "tr", "uz", "vi", "french", "german", "fi", "eu", "gl", "lb", "rm", "ca", "qu",
    }
)
_ESLAV_REC_LANGS = frozenset({"ru", "be", "uk"})
_ARABIC_REC_LANGS = frozenset({"ar", "fa", "ug", "ur", "ps", "ku", "sd", "bal"})
_SCRIPT_REC_LANGS = frozenset({"korean", "th", "el", "te", "ta"})


def suggested_ocr_python() -> str:
    """Best-guess path to a 3.12/3.13 venv interpreter with PaddleOCR installed."""
    here = Path(__file__).resolve()
    for parent in here.parents:
        win = parent / ".venv" / "Scripts" / "python.exe"
        if win.is_file():
            return str(win)
        unix = parent / ".venv" / "bin" / "python"
        if unix.is_file():
            return str(unix)
        if (parent / "scripts" / "install-ocr.ps1").is_file():
            return str(win)
    return r".\.venv\Scripts\python.exe"


def paddle_install_hint() -> str:
    venv_python = suggested_ocr_python()
    return (
        "PaddleOCR (required for bitmap subtitles) needs Python 3.12 or 3.13.\n\n"
        "Install into the project venv:\n"
        "  .\\scripts\\install-ocr.ps1\n\n"
        "Then launch the app with that interpreter (not system python):\n"
        f"  {venv_python} -m corrcut\n\n"
        "Manual install:\n"
        "  python -m pip install -e \".[ocr]\"\n"
        "  python -m pip install paddlepaddle==3.3.0 "
        "-i https://www.paddlepaddle.org.cn/packages/stable/cpu/"
    )


def paddle_unavailable_reason() -> str | None:
    """Why PaddleOCR cannot run in this process, or None if it can."""
    major, minor = sys.version_info[:2]
    if (major, minor) >= (3, 14):
        venv_python = suggested_ocr_python()
        return (
            f"Bitmap OCR cannot run on Python {major}.{minor} "
            f"({sys.executable}).\n\n"
            "PaddleOCR only supports Python 3.12 and 3.13. "
            ".\\scripts\\install-ocr.ps1 puts it in .venv, but `python -m corrcut` "
            "still uses this interpreter, so bitmap conversion cannot use PaddleOCR.\n\n"
            "Launch with:\n"
            f"  {venv_python} -m corrcut"
        )
    if not is_paddleocr_available():
        return paddle_install_hint()
    return None


def require_paddleocr() -> None:
    reason = paddle_unavailable_reason()
    if reason:
        raise RuntimeError(reason)


def _import_torch_before_paddle() -> None:
    """Load PyTorch before Paddle on Windows.

    Both ship ``libiomp5md.dll``. If Paddle's copy is already mapped, importing
    torch (PaddleOCR does this through modelscope) fails with WinError 127 on
    ``torch\\lib\\shm.dll``, and bitmap OCR looks like PaddleOCR is missing.
    """
    if sys.platform != "win32":
        return
    try:
        import torch  # noqa: F401, PLC0415
    except Exception:
        return


def is_paddleocr_available() -> bool:
    try:
        _import_torch_before_paddle()
        import paddle  # noqa: F401, PLC0415
        from paddleocr import PaddleOCR  # noqa: F401, PLC0415
    except Exception:
        return False
    return True


def paddle_device_label() -> str:
    """Device bitmap OCR will use. GPU is dropped when it fails a read test."""
    if _DEVICE_OVERRIDE is None:
        _warm_device_choice()
    return _select_device()


def _select_device() -> str:
    if _DEVICE_OVERRIDE is not None:
        return _DEVICE_OVERRIDE
    try:
        _import_torch_before_paddle()
        import paddle  # noqa: PLC0415
    except Exception:
        return "cpu"
    return _device_from_paddle(paddle)


def _warm_device_choice() -> None:
    """Create the English engine once so a bad GPU is rejected before the status line."""
    try:
        _import_torch_before_paddle()
        import paddle  # noqa: PLC0415
    except Exception:
        return
    if not _device_from_paddle(paddle).startswith("gpu"):
        return
    _get_engine("en")


def _device_from_paddle(paddle_mod: object) -> str:
    device_ns = getattr(paddle_mod, "device", None)
    compiled = getattr(device_ns, "is_compiled_with_cuda", None)
    try:
        cuda_build = bool(compiled()) if callable(compiled) else False
    except Exception:
        return "cpu"
    if not cuda_build:
        return "cpu"
    cuda_ns = getattr(device_ns, "cuda", None)
    count_fn = getattr(cuda_ns, "device_count", None)
    try:
        count = int(count_fn()) if callable(count_fn) else 0
    except Exception:
        return "cpu"
    if count > 0:
        return "gpu:0"
    return "cpu"


def paddle_language_code(tesseract_lang: str) -> str:
    raw = tesseract_lang.strip().lower()
    if raw in _PADDLE_LANG_BY_TESSERACT:
        return _PADDLE_LANG_BY_TESSERACT[raw]
    key = raw.replace("_", "-")
    if key in _PADDLE_LANG_BY_TESSERACT:
        return _PADDLE_LANG_BY_TESSERACT[key]
    if len(key) >= 3:
        return _PADDLE_LANG_BY_TESSERACT.get(key[:3], "en")
    return _PADDLE_LANG_BY_TESSERACT.get(key, "en")


def ocr_rgba_image(image: object, *, lang: str) -> str:
    """Run PaddleOCR on a subtitle RGBA/PIL image."""
    require_paddleocr()

    from sup2srt.ocr import postprocess  # noqa: PLC0415

    prepared = _prepare_subtitle_image(image)
    if prepared is None:
        return ""

    engine = _get_engine(lang)
    if engine is None:
        raise RuntimeError(f"Could not initialize PaddleOCR for language {lang!r}")

    raw = _run_ocr(engine, prepared)
    return postprocess(raw)


def ocr_rgba_images(images: list[object], *, lang: str) -> list[str]:
    """OCR subtitle images, batching ``predict`` when the engine accepts a list."""
    if not images:
        return []
    require_paddleocr()

    from sup2srt.ocr import postprocess  # noqa: PLC0415

    engine = _get_engine(lang)
    if engine is None:
        raise RuntimeError(f"Could not initialize PaddleOCR for language {lang!r}")

    prepared = [_prepare_subtitle_image(image) for image in images]
    texts = [""] * len(images)
    ready = [(index, array) for index, array in enumerate(prepared) if array is not None]
    for start in range(0, len(ready), _OCR_BATCH_SIZE):
        chunk = ready[start : start + _OCR_BATCH_SIZE]
        raws = _run_ocr_many(engine, [array for _, array in chunk])
        for (index, _), raw in zip(chunk, raws, strict=True):
            texts[index] = postprocess(raw) if raw else ""
    return texts


def _crop_opaque(image: object, *, pad: int = 12) -> object | None:
    """Crop an RGBA subtitle bitmap to its opaque content, with padding."""
    from PIL import Image  # noqa: PLC0415

    if not isinstance(image, Image.Image):
        return None
    if image.mode != "RGBA":
        return image
    bbox = image.getbbox()
    if bbox is None:
        return None
    left, top, right, bottom = bbox
    left = max(0, left - pad)
    top = max(0, top - pad)
    right = min(image.width, right + pad)
    bottom = min(image.height, bottom + pad)
    if right - left < 2 or bottom - top < 2:
        return None
    return image.crop((left, top, right, bottom))


def _composite_for_ocr(image: object) -> object:
    """Paste glyphs onto a background that keeps them visible to the detector."""
    import numpy as np
    from PIL import Image  # noqa: PLC0415

    assert isinstance(image, Image.Image)
    if image.mode != "RGBA":
        return image.convert("RGB")

    alpha = image.split()[3]
    rgb_src = image.convert("RGB")
    black_bg = Image.new("RGB", image.size, (0, 0, 0))
    black_bg.paste(rgb_src, mask=alpha)

    opaque = np.array(alpha) > 32
    if not opaque.any():
        return black_bg
    mean_luma = float(np.array(black_bg.convert("L"))[opaque].mean())
    # Dark text composited onto black disappears; use white instead.
    if mean_luma < 60:
        white_bg = Image.new("RGB", image.size, (255, 255, 255))
        white_bg.paste(rgb_src, mask=alpha)
        return white_bg
    return black_bg


def _prepare_subtitle_image(image: object) -> object | None:
    import numpy as np
    from PIL import Image, ImageFilter  # noqa: PLC0415

    if not isinstance(image, Image.Image):
        return None

    cropped = _crop_opaque(image) if image.mode == "RGBA" else image
    if cropped is None:
        return None
    rgb = _composite_for_ocr(cropped)
    if not isinstance(rgb, Image.Image):
        return None

    min_height = 64
    if rgb.height < min_height:
        scale = min_height / max(rgb.height, 1)
        rgb = rgb.resize(
            (max(1, int(rgb.width * scale)), max(1, int(rgb.height * scale))),
            Image.Resampling.LANCZOS,
        )
    elif rgb.width < 640:
        rgb = rgb.resize((rgb.width * 2, rgb.height * 2), Image.Resampling.LANCZOS)

    rgb = rgb.filter(ImageFilter.SHARPEN)
    return np.array(rgb)


def _get_engine(lang: str) -> object | None:
    with _ENGINE_LOCK:
        cached = _ENGINE_CACHE.get(f"{lang}|{_select_device()}")
        if cached is not None:
            return cached
        ocr_engine = _create_engine(lang)
        if ocr_engine is not None:
            # Device may have switched from gpu to cpu during creation.
            _ENGINE_CACHE[f"{lang}|{_select_device()}"] = ocr_engine
        return ocr_engine


def _recognition_model_name(lang: str) -> str:
    """Recognition model paired with the mobile detector.

    Passing only ``text_detection_model_name`` makes PaddleOCR ignore ``lang``
    and fall back to the Chinese recognizer.
    """
    if lang in ("ch", "chinese_cht", "japan"):
        return "PP-OCRv5_server_rec"
    if lang == "en":
        return _EN_REC_MODEL
    if lang in _SCRIPT_REC_LANGS:
        return f"{lang}_PP-OCRv5_mobile_rec"
    if lang in _ESLAV_REC_LANGS:
        return "eslav_PP-OCRv5_mobile_rec"
    if lang in _ARABIC_REC_LANGS:
        return "arabic_PP-OCRv5_mobile_rec"
    if lang in _LATIN_REC_LANGS:
        return "latin_PP-OCRv5_mobile_rec"
    if lang == "hi":
        return "devanagari_PP-OCRv5_mobile_rec"
    return _EN_REC_MODEL


def _engine_init_variants(device: str, lang: str) -> tuple[dict[str, Any], ...]:
    """Kwargs tried in order. oneDNN stays off only on CPU (Paddle 3.x PIR crash)."""
    common = {
        "use_doc_orientation_classify": False,
        "use_doc_unwarping": False,
        "use_textline_orientation": False,
        "text_detection_model_name": _MOBILE_DET_MODEL,
        "text_recognition_model_name": _recognition_model_name(lang),
        "device": device,
    }
    if device == "cpu":
        return (
            {**common, "enable_mkldnn": False},
            common,
            {"device": device, "enable_mkldnn": False},
            {"device": device},
            {},
        )
    return (
        common,
        {
            "device": device,
            "text_detection_model_name": _MOBILE_DET_MODEL,
            "text_recognition_model_name": _recognition_model_name(lang),
            "use_doc_orientation_classify": False,
            "use_doc_unwarping": False,
            "use_textline_orientation": False,
        },
        {"device": device},
        {},
    )


def _probe_subtitle_image() -> object:
    """White-on-transparent line a working recognizer should read as Hello."""
    from PIL import Image, ImageDraw, ImageFont  # noqa: PLC0415

    image = Image.new("RGBA", (640, 120), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    font = None
    for candidate in ("arial.ttf", r"C:\Windows\Fonts\arial.ttf"):
        try:
            font = ImageFont.truetype(candidate, 64)
            break
        except OSError:
            continue
    if font is None:
        try:
            font = ImageFont.load_default(size=64)
        except TypeError:
            font = ImageFont.load_default()
    draw.text((20, 20), "Hello there", fill=(255, 255, 255, 255), font=font)
    return image


def _gpu_readable(ocr_engine: object) -> bool | None:
    """Whether GPU OCR reads a known English line.

    ``None`` means this object is not a real predictor (unit-test fakes).
    """
    predict = getattr(ocr_engine, "predict", None)
    if not callable(predict):
        return None
    prepared = _prepare_subtitle_image(_probe_subtitle_image())
    if prepared is None:
        return None
    try:
        result = predict(prepared)
    except Exception:
        return False
    return "hello" in _extract_text(result).lower()


def _create_engine(lang: str) -> object | None:
    global _DEVICE_OVERRIDE

    device = _select_device()
    ocr_engine = _build_engine(lang, device)
    if ocr_engine is None or not str(device).startswith("gpu"):
        return ocr_engine
    if _gpu_readable(ocr_engine) is False:
        import logging  # noqa: PLC0415

        logging.getLogger(__name__).warning(
            "GPU OCR could not read a test image on %s; using CPU.",
            device,
        )
        _DEVICE_OVERRIDE = "cpu"
        return _build_engine(lang, "cpu")
    return ocr_engine


def _build_engine(lang: str, device: str) -> object | None:
    try:
        _import_torch_before_paddle()
        from paddleocr import PaddleOCR  # noqa: PLC0415
    except Exception:
        return None

    init_attempts = (lang, "en") if lang != "en" else (lang,)
    # Progressively simpler kwargs. PaddleOCR 3.x rejects the legacy
    # ``show_log``/``use_angle_cls`` names. CPU inference crashes with a
    # oneDNN/PIR bug unless mkldnn is disabled; GPU does not need that flag.
    last_error: Exception | None = None
    for attempt_lang in init_attempts:
        for kwargs in _engine_init_variants(device, attempt_lang):
            try:
                return PaddleOCR(lang=attempt_lang, **kwargs)
            except Exception as exc:  # noqa: BLE001
                last_error = exc
                continue
    if last_error is not None:
        import logging  # noqa: PLC0415

        logging.getLogger(__name__).warning(
            "PaddleOCR initialization failed for lang=%r device=%s: %s",
            lang,
            device,
            last_error,
        )
    return None


def _run_ocr_many(engine: object, image_arrays: list[object]) -> list[str]:
    """Run one batched ``predict`` when possible; otherwise one call per image."""
    if not image_arrays:
        return []
    if len(image_arrays) == 1:
        return [_run_ocr(engine, image_arrays[0])]

    predict = getattr(engine, "predict", None)
    if predict is not None:
        try:
            result = predict(image_arrays)
        except TypeError:
            result = None
        except Exception:
            result = None
        else:
            texts = _texts_from_batch_result(result, len(image_arrays))
            if texts is not None:
                return texts
    return [_run_ocr(engine, image) for image in image_arrays]


def _texts_from_batch_result(result: object, count: int) -> list[str] | None:
    if not isinstance(result, list) or len(result) != count:
        return None
    return [_extract_text(item) for item in result]


def _run_ocr(engine: object, image_array: object) -> str:
    for method_name in ("predict", "ocr"):
        method = getattr(engine, method_name, None)
        if method is None:
            continue
        try:
            result = (
                method(image_array, cls=False) if method_name == "ocr" else method(image_array)
            )
        except TypeError:
            try:
                result = method(image_array)
            except Exception:
                continue
        except Exception:
            continue
        text = _extract_text(result)
        if text:
            return text
    return ""


def _extract_text(result: Any) -> str:
    if result is None:
        return ""

    if isinstance(result, list) and result and not isinstance(result[0], (list, tuple)):
        chunks = [_extract_text(item) for item in result]
        return _join_lines(chunks)

    if isinstance(result, list):
        lines: list[str] = []
        for item in result:
            if item is None:
                continue
            if isinstance(item, (list, tuple)):
                if len(item) >= 2 and isinstance(item[1], (list, tuple)) and item[1]:
                    token = item[1][0]
                    if isinstance(token, str) and token.strip():
                        lines.append(token.strip())
                    continue
                nested = _extract_text(item)
                if nested:
                    lines.append(nested)
                continue
            text = _object_text(item)
            if text:
                lines.append(text)
        return _join_lines(lines)

    return _object_text(result)


def _object_text(value: object) -> str:
    # PaddleOCR 3.x result objects behave like dicts; prefer geometry-aware
    # extraction so multi-region text is ordered by reading order.
    if isinstance(value, dict):
        text = _extract_text_from_json(value)
        if text:
            return text

    for attr in ("rec_texts", "rec_text", "text"):
        if hasattr(value, attr):
            raw = getattr(value, attr)
            if isinstance(raw, str) and raw.strip():
                return raw.strip()
            if isinstance(raw, list):
                boxes = None
                for box_attr in ("rec_boxes", "rec_polys", "dt_polys"):
                    if hasattr(value, box_attr):
                        boxes = getattr(value, box_attr)
                        break
                ordered = _order_texts(raw, boxes)
                if ordered:
                    return ordered

    json_method = getattr(value, "json", None)
    if callable(json_method):
        try:
            payload = json_method()
            text = _extract_text_from_json(payload)
            if text:
                return text
        except Exception:
            pass

    if isinstance(value, dict):
        return _extract_text_from_json(value)
    return ""


def _extract_text_from_json(payload: object) -> str:
    if not isinstance(payload, dict):
        return ""

    texts = payload.get("rec_texts")
    if isinstance(texts, list) and texts:
        boxes = payload.get("rec_boxes")
        if boxes is None:
            boxes = payload.get("rec_polys")
        if boxes is None:
            boxes = payload.get("dt_polys")
        ordered = _order_texts(texts, boxes)
        if ordered:
            return ordered

    for key in ("rec_text", "text", "transcription"):
        raw = payload.get(key)
        if isinstance(raw, str) and raw.strip():
            return raw.strip()
        if isinstance(raw, list):
            ordered = _order_texts(raw, None)
            if ordered:
                return ordered

    for nested_key in ("res", "result", "ocr_result", "data"):
        nested = payload.get(nested_key)
        if nested is None:
            continue
        text = _extract_text(nested)
        if text:
            return text
    return ""


def _box_metrics(box: object) -> tuple[float, float, float] | None:
    """Return (top_y, left_x, height) for a rec_box [x1,y1,x2,y2] or polygon."""
    if box is None:
        return None
    try:
        coords = [float(v) for v in list(box)]
    except (TypeError, ValueError):
        return None
    if not coords:
        return None
    # Axis-aligned box: [x1, y1, x2, y2].
    if len(coords) == 4 and not isinstance(list(box)[0], (list, tuple)):
        x1, y1, x2, y2 = coords
        return min(y1, y2), min(x1, x2), abs(y2 - y1)
    # Polygon: flat [x1,y1,x2,y2,...] or list of [x,y] points.
    points = list(box)
    xs: list[float] = []
    ys: list[float] = []
    if isinstance(points[0], (list, tuple)):
        for pt in points:
            xs.append(float(pt[0]))
            ys.append(float(pt[1]))
    else:
        for i in range(0, len(coords) - 1, 2):
            xs.append(coords[i])
            ys.append(coords[i + 1])
    if not xs or not ys:
        return None
    return min(ys), min(xs), max(ys) - min(ys)


def _order_texts(texts: object, boxes: object) -> str:
    """Order recognized text fragments by reading order (top→bottom, left→right).

    Same-line fragments are joined with spaces; separate lines with newlines.
    Falls back to detection order when box geometry is unavailable.
    """
    if not isinstance(texts, list):
        return ""
    box_list = list(boxes) if boxes is not None else []

    items: list[tuple[float, float, float, str]] = []
    for i, raw in enumerate(texts):
        s = str(raw).strip()
        if not s:
            continue
        metrics = _box_metrics(box_list[i]) if i < len(box_list) else None
        if metrics is None:
            items.append((float(i), 0.0, 0.0, s))
        else:
            top, left, height = metrics
            items.append((top, left, height, s))

    if not items:
        return ""

    has_geometry = any(h > 0 for _, _, h, _ in items)
    if not has_geometry:
        return " ".join(text for *_, text in items)

    heights = sorted(h for _, _, h, _ in items if h > 0)
    median_h = heights[len(heights) // 2] if heights else 0.0
    row_thresh = max(4.0, 0.6 * median_h)

    items.sort(key=lambda it: (it[0], it[1]))
    rows: list[list[tuple[float, float, float, str]]] = [[items[0]]]
    for it in items[1:]:
        if abs(it[0] - rows[-1][0][0]) <= row_thresh:
            rows[-1].append(it)
        else:
            rows.append([it])

    lines: list[str] = []
    for row in rows:
        row.sort(key=lambda it: it[1])
        lines.append(" ".join(it[3] for it in row))
    return "\n".join(lines)


def _join_lines(parts: list[str]) -> str:
    cleaned = [part.strip() for part in parts if part and part.strip()]
    if not cleaned:
        return ""
    return "\n".join(cleaned)
