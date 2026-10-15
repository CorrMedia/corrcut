"""PaddleOCR engine helpers."""

from __future__ import annotations

from corrcut.engine.subtitles import paddle_ocr_engine as engine
from corrcut.engine.subtitles.bitmap_ocr import is_pgs_ocr_available, vobsub_language_code


def test_paddle_language_code_maps_tesseract_codes() -> None:
    assert engine.paddle_language_code("eng") == "en"
    assert engine.paddle_language_code("deu") == "german"
    assert engine.paddle_language_code("jpn") == "japan"
    assert engine.paddle_language_code("chi_sim") == "ch"


def test_vobsub_language_code_maps_iso() -> None:
    assert vobsub_language_code("eng") == "eng"
    assert vobsub_language_code("en") == "eng"
    assert vobsub_language_code("deu") == "deu"
    assert vobsub_language_code(None) == "eng"


def test_extract_text_from_paddle_2x_style_result() -> None:
    result = [[[[0, 0], [1, 0], [1, 1], [0, 1]], ("Hello there", 0.98)]]
    assert engine._extract_text(result) == "Hello there"


def test_extract_text_from_rec_texts_payload() -> None:
    payload = {"rec_texts": ["If I hit", "a Greyhound station"]}
    assert engine._extract_text_from_json(payload) == "If I hit a Greyhound station"


def test_extract_text_orders_boxes_top_to_bottom() -> None:
    # Detection order is scrambled; boxes place "I could" above the rest.
    payload = {
        "rec_texts": ["never do that to you.", "I could"],
        "rec_boxes": [[20, 95, 400, 140], [20, 15, 180, 60]],
    }
    assert engine._extract_text_from_json(payload) == "I could\nnever do that to you."


def test_extract_text_joins_same_line_left_to_right() -> None:
    payload = {
        "rec_texts": ["a Greyhound", "If I hit"],
        "rec_boxes": [[200, 10, 400, 50], [10, 10, 180, 50]],
    }
    assert engine._extract_text_from_json(payload) == "If I hit a Greyhound"


def test_is_pgs_ocr_available_tracks_paddle() -> None:
    assert is_pgs_ocr_available() == engine.is_paddleocr_available()


def test_create_engine_cpu_disables_mkldnn_and_uses_mobile_det(monkeypatch) -> None:
    """CPU inference needs mkldnn off; both devices use the mobile detector."""
    calls: list[dict] = []

    class FakePaddleOCR:
        def __init__(self, **kwargs) -> None:
            calls.append(kwargs)

    import paddleocr

    monkeypatch.setattr(paddleocr, "PaddleOCR", FakePaddleOCR)
    monkeypatch.setattr(engine, "_select_device", lambda: "cpu")

    result = engine._create_engine("en")

    assert isinstance(result, FakePaddleOCR)
    first = calls[0]
    assert first.get("enable_mkldnn") is False
    assert first.get("device") == "cpu"
    assert first.get("text_detection_model_name") == "PP-OCRv5_mobile_det"
    assert first.get("text_recognition_model_name") == "en_PP-OCRv5_mobile_rec"
    assert first.get("use_doc_orientation_classify") is False
    assert first.get("use_doc_unwarping") is False
    assert first.get("use_textline_orientation") is False
    assert "show_log" not in first
    assert first.get("lang") == "en"


def test_create_engine_gpu_omits_mkldnn(monkeypatch) -> None:
    calls: list[dict] = []

    class FakePaddleOCR:
        def __init__(self, **kwargs) -> None:
            calls.append(kwargs)

    import paddleocr

    monkeypatch.setattr(paddleocr, "PaddleOCR", FakePaddleOCR)
    monkeypatch.setattr(engine, "_select_device", lambda: "gpu:0")

    result = engine._create_engine("en")

    assert isinstance(result, FakePaddleOCR)
    first = calls[0]
    assert first.get("device") == "gpu:0"
    assert first.get("text_detection_model_name") == "PP-OCRv5_mobile_det"
    assert first.get("text_recognition_model_name") == "en_PP-OCRv5_mobile_rec"
    assert "enable_mkldnn" not in first
    assert "show_log" not in first


def test_unreadable_gpu_falls_back_to_cpu(monkeypatch) -> None:
    calls: list[dict] = []

    class FakePaddleOCR:
        def __init__(self, **kwargs) -> None:
            calls.append(kwargs)

    import paddleocr

    monkeypatch.setattr(engine, "_DEVICE_OVERRIDE", None)
    monkeypatch.setattr(paddleocr, "PaddleOCR", FakePaddleOCR)
    monkeypatch.setattr(engine, "_select_device", lambda: "gpu:0")
    monkeypatch.setattr(engine, "_gpu_readable", lambda _ocr: False)

    result = engine._create_engine("en")

    assert isinstance(result, FakePaddleOCR)
    assert [call.get("device") for call in calls] == ["gpu:0", "cpu"]
    assert calls[1].get("enable_mkldnn") is False
    assert engine._DEVICE_OVERRIDE == "cpu"


def test_recognition_model_name_is_english_not_chinese() -> None:
    assert engine._recognition_model_name("en") == "en_PP-OCRv5_mobile_rec"
    assert engine._recognition_model_name("german") == "latin_PP-OCRv5_mobile_rec"
    assert "ch" not in engine._recognition_model_name("en")


def test_device_from_paddle_gpu_when_cuda_device_present() -> None:
    class FakeCuda:
        @staticmethod
        def device_count() -> int:
            return 1

    class FakeDevice:
        cuda = FakeCuda()

        @staticmethod
        def is_compiled_with_cuda() -> bool:
            return True

    class FakePaddle:
        device = FakeDevice()

    assert engine._device_from_paddle(FakePaddle()) == "gpu:0"


def test_device_from_paddle_cpu_without_cuda_build() -> None:
    class FakeDevice:
        @staticmethod
        def is_compiled_with_cuda() -> bool:
            return False

    class FakePaddle:
        device = FakeDevice()

    assert engine._device_from_paddle(FakePaddle()) == "cpu"


def test_device_from_paddle_cpu_when_cuda_build_has_no_device() -> None:
    class FakeCuda:
        @staticmethod
        def device_count() -> int:
            return 0

    class FakeDevice:
        cuda = FakeCuda()

        @staticmethod
        def is_compiled_with_cuda() -> bool:
            return True

    class FakePaddle:
        device = FakeDevice()

    assert engine._device_from_paddle(FakePaddle()) == "cpu"


def test_run_ocr_many_uses_list_predict() -> None:
    calls: list[object] = []

    class Engine:
        def predict(self, images: object) -> list[dict[str, list[str]]]:
            calls.append(images)
            assert isinstance(images, list)
            return [{"rec_texts": ["left"]}, {"rec_texts": ["right"]}]

    texts = engine._run_ocr_many(Engine(), ["img-a", "img-b"])

    assert texts == ["left", "right"]
    assert calls == [["img-a", "img-b"]]


def test_run_ocr_many_falls_back_when_batch_predict_rejects_list() -> None:
    class Engine:
        def predict(self, images: object) -> dict[str, list[str]]:
            if isinstance(images, list):
                raise TypeError("no batch")
            return {"rec_texts": [f"one-{images}"]}

    texts = engine._run_ocr_many(Engine(), ["x", "y"])

    assert texts == ["one-x", "one-y"]


def test_paddle_unavailable_reason_on_314(monkeypatch) -> None:
    monkeypatch.setattr(engine.sys, "version_info", (3, 14, 7, "final", 0))
    reason = engine.paddle_unavailable_reason()
    assert reason is not None
    assert "3.14" in reason
    assert "corrcut" in reason


def test_paddle_unavailable_reason_none_when_paddle_present(monkeypatch) -> None:
    monkeypatch.setattr(engine.sys, "version_info", (3, 13, 3, "final", 0))
    monkeypatch.setattr(engine, "is_paddleocr_available", lambda: True)
    assert engine.paddle_unavailable_reason() is None


def test_prepare_subtitle_image_crops_transparent_padding() -> None:
    from PIL import Image

    img = Image.new("RGBA", (400, 200), (0, 0, 0, 0))
    for x in range(40, 90):
        for y in range(20, 50):
            img.putpixel((x, y), (255, 255, 255, 255))
    prepared = engine._prepare_subtitle_image(img)
    assert prepared is not None
    height, width = prepared.shape[:2]
    assert width < 400
    assert height < 200


def test_prepare_subtitle_image_uses_white_bg_for_dark_text() -> None:
    from PIL import Image

    img = Image.new("RGBA", (80, 40), (0, 0, 0, 0))
    for x in range(10, 70):
        for y in range(8, 32):
            img.putpixel((x, y), (20, 20, 20, 255))
    composited = engine._composite_for_ocr(img)
    assert composited.getpixel((40, 20)) == (20, 20, 20)
    assert composited.getpixel((0, 0)) == (255, 255, 255)
