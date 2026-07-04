import os
import tempfile
import whisper

_model = None


def _load_model():
    global _model
    if _model is None:
        model_size = os.getenv("WHISPER_MODEL", "tiny")
        _model = whisper.load_model(model_size)
    return _model


def transcribe(audio_bytes: bytes) -> dict:
    model = _load_model()
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
        f.write(audio_bytes)
        tmp_path = f.name
    try:
        result = model.transcribe(tmp_path)
    finally:
        os.unlink(tmp_path)
    return {"text": result["text"]}
