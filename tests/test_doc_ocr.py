"""
Unit tests for the doc OCR worker inference function.
These run without Docker — they mock the easyocr reader.
"""

import io
import pytest
from unittest.mock import MagicMock, patch
from PIL import Image


def _make_image_bytes() -> bytes:
    img = Image.new("RGB", (300, 300), color=(255, 255, 255))
    buf = io.BytesIO()
    img.save(buf, format="JPEG")
    return buf.getvalue()


@patch("workers.doc_ocr.inference.reader")
def test_extract_text_returns_text_and_confidence(mock_reader):
    import sys, os
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "workers", "doc_ocr"))
    from inference import extract_text

    # Mock EasyOCR results: list of (bbox, text, confidence)
    mock_reader.readtext.return_value = [
        ([], "Hello", 0.95),
        ([], "World", 0.92),
    ]

    result = extract_text(_make_image_bytes())

    assert "text" in result
    assert "Hello\nWorld" == result["text"]
    assert "avg_confidence" in result
    assert result["avg_confidence"] > 0.9
    assert result["line_count"] == 2


def test_extract_text_invalid_bytes_raises():
    import sys, os
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "workers", "doc_ocr"))
    from inference import extract_text

    with pytest.raises(ValueError):
        extract_text(b"not-an-image")
