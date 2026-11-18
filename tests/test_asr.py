"""
Unit tests for the ASR worker inference function.
These run without Docker or a downloaded Whisper model.
"""

import io
import wave
from unittest.mock import MagicMock

import pytest

from helpers import load_module_from_path


def _make_silence_wav() -> bytes:
    """Generate a minimal valid WAV file (one second of silence)."""
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(16000)
        wav.writeframes(b"\x00\x00" * 16000)
    return buf.getvalue()


@pytest.fixture
def asr_module(monkeypatch):
    whisper_stub = MagicMock()
    module = load_module_from_path(
        "asr_inference",
        "workers/asr/inference.py",
        stubs={"whisper": whisper_stub},
        monkeypatch=monkeypatch,
    )
    return module, whisper_stub


def test_transcribe_returns_text(asr_module):
    module, whisper_stub = asr_module
    mock_model = MagicMock()
    mock_model.transcribe.return_value = {"text": " hello world"}
    whisper_stub.load_model.return_value = mock_model

    assert module.transcribe(_make_silence_wav()) == {"text": " hello world"}


def test_transcribe_empty_audio(asr_module):
    module, whisper_stub = asr_module
    mock_model = MagicMock()
    mock_model.transcribe.return_value = {"text": ""}
    whisper_stub.load_model.return_value = mock_model

    assert module.transcribe(_make_silence_wav()) == {"text": ""}
