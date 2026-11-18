"""
Unit tests for the doc OCR worker inference function.
These run without Docker or a downloaded EasyOCR model.
"""

import io
from unittest.mock import MagicMock

import pytest
from PIL import Image

from helpers import load_module_from_path


def _make_image_bytes() -> bytes:
    img = Image.new("RGB", (300, 300), color=(255, 255, 255))
    buf = io.BytesIO()
    img.save(buf, format="JPEG")
    return buf.getvalue()


@pytest.fixture
def doc_ocr_module(monkeypatch):
    easyocr_stub = MagicMock()
    module = load_module_from_path(
        "doc_ocr_inference",
        "workers/doc_ocr/inference.py",
        stubs={"easyocr": easyocr_stub},
        monkeypatch=monkeypatch,
    )
    return module, easyocr_stub


def test_extract_text_returns_text_and_confidence(doc_ocr_module):
    module, easyocr_stub = doc_ocr_module
    easyocr_stub.Reader.return_value.readtext.return_value = [
        ([], "Hello", 0.95),
        ([], "World", 0.92),
    ]

    result = module.extract_text(_make_image_bytes())

    assert result["text"] == "Hello\nWorld"
    assert result["avg_confidence"] > 0.9
    assert result["line_count"] == 2


def test_extract_text_invalid_bytes_raises(doc_ocr_module):
    module, easyocr_stub = doc_ocr_module

    with pytest.raises(ValueError):
        module.extract_text(b"not-an-image")

    easyocr_stub.Reader.return_value.readtext.assert_not_called()
