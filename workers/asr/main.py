from fastapi import FastAPI, UploadFile, File, HTTPException
from inference import transcribe, _load_model

AUDIO_MIME_TYPES = {
    "audio/wav", "audio/wave", "audio/x-wav",
    "audio/mpeg", "audio/mp3", "audio/ogg",
    "audio/webm", "audio/flac", "audio/x-flac",
    "application/octet-stream",
}

app = FastAPI(title="ASR Worker", version="1.0.0")


@app.on_event("startup")
async def startup():
    _load_model()


@app.post("/infer")
async def infer(file: UploadFile = File(...)):
    if file.content_type and file.content_type not in AUDIO_MIME_TYPES:
        raise HTTPException(400, f"Unexpected content type: {file.content_type}")
    audio_bytes = await file.read()
    try:
        result = transcribe(audio_bytes)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return result


@app.get("/health")
def health():
    return {"status": "ok"}
