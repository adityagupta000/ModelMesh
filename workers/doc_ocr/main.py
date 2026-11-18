from fastapi import FastAPI, UploadFile, File, HTTPException
from inference import extract_text

app = FastAPI(title="Document OCR Worker", version="1.0.0")


@app.post("/infer")
async def infer(file: UploadFile = File(...)):
    image_bytes = await file.read()
    try:
        result = extract_text(image_bytes)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    return result


@app.get("/health")
def health():
    return {"status": "ok"}
