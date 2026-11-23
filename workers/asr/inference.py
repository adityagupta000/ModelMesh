import os
import tempfile
import threading
import whisper

_model = None
_lock = threading.Lock()


def _load_model():
    global _model
    with _lock:
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
    except RuntimeError as exc:
        if str(exc).startswith("Failed to load audio"):
            raise ValueError("Invalid audio data") from exc
        raise
    finally:
        os.unlink(tmp_path)
    return {"text": result["text"]}
