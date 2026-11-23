"""
Unit tests for the ASR worker inference function.
These run without Docker or a downloaded Whisper model.
"""

import io
import wave
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException

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


def test_invalid_audio_decode_runtime_becomes_permanent_input_error(asr_module):
    module, whisper_stub = asr_module
    mock_model = MagicMock()
    mock_model.transcribe.side_effect = RuntimeError(
        "Failed to load audio: invalid input"
    )
    whisper_stub.load_model.return_value = mock_model

    with pytest.raises(ValueError, match="Invalid audio data"):
        module.transcribe(b"not a wav file")


def test_unrelated_model_runtime_error_is_not_reclassified(asr_module):
    module, whisper_stub = asr_module
    mock_model = MagicMock()
    mock_model.transcribe.side_effect = RuntimeError("model execution failed")
    whisper_stub.load_model.return_value = mock_model

    with pytest.raises(RuntimeError, match="model execution failed"):
        module.transcribe(b"audio")


@pytest.mark.asyncio(scope="session")
async def test_asr_http_endpoint_returns_400_for_invalid_audio(monkeypatch):
    inference_stub = SimpleNamespace(
        transcribe=MagicMock(side_effect=ValueError("Invalid audio data")),
        _load_model=MagicMock(),
    )
    worker = load_module_from_path(
        "asr_http_worker",
        "workers/asr/main.py",
        stubs={"inference": inference_stub},
        monkeypatch=monkeypatch,
    )
    upload = SimpleNamespace(
        content_type="audio/wav",
        read=AsyncMock(return_value=b"not a wav file"),
    )

    with pytest.raises(HTTPException) as exc:
        await worker.infer(upload)

    assert exc.value.status_code == 400
    assert exc.value.detail == "Invalid audio data"
