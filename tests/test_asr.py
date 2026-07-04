"""
Unit tests for the ASR worker inference function.
These run without Docker — they mock the whisper model.
"""

import pytest
from unittest.mock import MagicMock, patch


def _make_silence_wav() -> bytes:
    """Generate a minimal valid WAV file (44-byte header, 1 second silence)."""
    import struct, wave, io
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(16000)
        w.writeframes(b"\x00\x00" * 16000)
    return buf.getvalue()


@patch("workers.asr.inference.whisper")
def test_transcribe_returns_text(mock_whisper):
    import sys, os
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "workers", "asr"))

    mock_model = MagicMock()
    mock_model.transcribe.return_value = {"text": " hello world"}
    mock_whisper.load_model.return_value = mock_model

    # Reset module-level singleton so mock takes effect
    import inference as asr_inf
    asr_inf._model = None

    result = asr_inf.transcribe(_make_silence_wav())
    assert result == {"text": " hello world"}


@patch("workers.asr.inference.whisper")
def test_transcribe_empty_audio(mock_whisper):
    import sys, os
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "workers", "asr"))

    mock_model = MagicMock()
    mock_model.transcribe.return_value = {"text": ""}
    mock_whisper.load_model.return_value = mock_model

    import inference as asr_inf
    asr_inf._model = None

    result = asr_inf.transcribe(_make_silence_wav())
    assert "text" in result
